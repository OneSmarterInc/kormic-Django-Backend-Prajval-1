from __future__ import annotations

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.db_utils import run_with_retry
from accounts.mfa import (
    clear_totp_failures,
    create_mfa_session,
    get_user_id_from_mfa_token,
    invalidate_mfa_session,
    is_totp_throttled,
    record_totp_failure,
)
from accounts.models import TOTPBackupCode, TOTPDevice
from accounts.serializers import (
    EnrollVerifySerializer,
    LoginSerializer,
    RegisterSerializer,
    VerifyTOTPSerializer,
    serialize_user,
)
from accounts.totp import (
    build_provisioning_uri,
    generate_backup_codes,
    generate_totp_secret,
    hash_backup_code,
    verify_totp_code,
)


def _user_has_confirmed_totp(user: User) -> bool:
    return TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = run_with_retry(serializer.save)

        # Auto-login on register: hand back the same kind of limited,
        # no-refresh access token LoginView issues for a not-yet-enrolled
        # user, so the client can go straight into TOTP enrollment without
        # a separate /login/ call.
        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "message": "Account created. Complete TOTP enrollment to finish setup.",
                "must_enroll_totp": True,
                "access": str(refresh.access_token),
                "user": serialize_user(user),
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """
    Step 1 of login.

    - No confirmed TOTP device yet: issue a usable access token directly
      (no refresh token) with must_enroll_totp=True. That access token is
      only accepted by the TOTP-gate-exempt endpoints (enroll, verify-
      enrollment, logout, me) until enrollment completes.
    - Confirmed TOTP device: issue an opaque mfa_token instead of any
      tokens; the client must call /verify-totp/ next.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            request,
            username=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )

        if user is None or not user.is_active:
            return Response({"detail": "Invalid email or password."}, status=status.HTTP_401_UNAUTHORIZED)

        if _user_has_confirmed_totp(user):
            mfa_token = create_mfa_session(user.id)
            return Response(
                {
                    "must_enroll_totp": False,
                    "mfa_token": mfa_token,
                    "totp_required": True,
                    "expires_in": 300,
                },
                status=status.HTTP_200_OK,
            )

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "must_enroll_totp": True,
                "access": str(refresh.access_token),
                "user": serialize_user(user),
            },
            status=status.HTTP_200_OK,
        )


class TOTPEnrollView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if _user_has_confirmed_totp(user):
            return Response({"detail": "TOTP is already enrolled."}, status=status.HTTP_400_BAD_REQUEST)

        secret = generate_totp_secret()
        run_with_retry(
            lambda: TOTPDevice.objects.update_or_create(user=user, defaults={"secret": secret, "confirmed_at": None})
        )

        return Response(
            {"secret": secret, "provisioning_uri": build_provisioning_uri(secret, user.email)},
            status=status.HTTP_200_OK,
        )


class TOTPVerifyEnrollmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = EnrollVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"].strip()

        try:
            device = request.user.totp_device
        except TOTPDevice.DoesNotExist:
            return Response({"detail": "TOTP enrollment has not been started."}, status=status.HTTP_400_BAD_REQUEST)

        if device.confirmed_at:
            return Response({"detail": "TOTP is already enrolled."}, status=status.HTTP_400_BAD_REQUEST)

        if not verify_totp_code(device.secret, code):
            return Response({"detail": "Invalid TOTP code."}, status=status.HTTP_400_BAD_REQUEST)

        backup_codes = generate_backup_codes()

        def _confirm():
            with transaction.atomic():
                device.confirmed_at = timezone.now()
                device.save(update_fields=["confirmed_at"])
                TOTPBackupCode.objects.filter(user=request.user).delete()
                TOTPBackupCode.objects.bulk_create(
                    [TOTPBackupCode(user=request.user, code_hash=hash_backup_code(c)) for c in backup_codes]
                )

        run_with_retry(_confirm)

        return Response({"backup_codes": backup_codes}, status=status.HTTP_200_OK)


class TOTPLoginVerifyView(APIView):
    """Step 2 of login: exchange mfa_token + TOTP/backup code for real tokens."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def post(self, request):
        serializer = VerifyTOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mfa_token = serializer.validated_data["mfa_token"].strip()
        code = serializer.validated_data["code"].strip().upper()

        user_id = get_user_id_from_mfa_token(mfa_token)
        if not user_id:
            return Response(
                {"detail": "Session expired or invalid. Please log in again."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if is_totp_throttled(user_id):
            return Response(
                {"detail": "Too many incorrect attempts. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            user = User.objects.get(id=user_id)
            device = user.totp_device
        except (User.DoesNotExist, TOTPDevice.DoesNotExist):
            return Response({"detail": "TOTP is not enrolled."}, status=status.HTTP_400_BAD_REQUEST)

        if not device.confirmed_at:
            return Response({"detail": "TOTP is not enrolled."}, status=status.HTTP_400_BAD_REQUEST)

        is_valid = False
        used_backup_code = None

        if code.isdigit() and len(code) == 6:
            is_valid = verify_totp_code(device.secret, code, user_id=user.id)

        if not is_valid and len(code) == 10:
            code_hash = hash_backup_code(code)
            used_backup_code = TOTPBackupCode.objects.filter(
                user=user, code_hash=code_hash, used_at__isnull=True
            ).first()
            is_valid = used_backup_code is not None

        if not is_valid:
            record_totp_failure(user_id)
            return Response({"detail": "Invalid TOTP or backup code."}, status=status.HTTP_400_BAD_REQUEST)

        def _mark_used():
            with transaction.atomic():
                if used_backup_code:
                    used_backup_code.used_at = timezone.now()
                    used_backup_code.save(update_fields=["used_at"])
                else:
                    device.last_used_at = timezone.now()
                    device.save(update_fields=["last_used_at"])

        run_with_retry(_mark_used)

        clear_totp_failures(user_id)
        invalidate_mfa_session(mfa_token)

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": serialize_user(user),
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response({"detail": "refresh is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            run_with_retry(RefreshToken(refresh_token).blacklist)
        except Exception:
            return Response({"detail": "Invalid or already-blacklisted refresh token."}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_205_RESET_CONTENT)


class CurrentUserView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(serialize_user(request.user), status=status.HTTP_200_OK)
