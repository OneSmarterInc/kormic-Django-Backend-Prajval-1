from __future__ import annotations

import os

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.db_utils import run_with_retry
from accounts.github_oauth import (
    GitHubOAuthError,
    build_authorize_url,
    consume_oauth_state,
    create_oauth_state,
    exchange_code_for_token,
    fetch_github_identity,
    get_connection_for_user,
    revoke_and_delete,
    save_connection,
)
from accounts.mfa import (
    clear_totp_failures,
    create_mfa_session,
    get_user_id_from_mfa_token,
    invalidate_mfa_session,
    is_totp_throttled,
    record_totp_failure,
)
from accounts.models import TOTPBackupCode, TOTPDevice
from accounts.permissions import IsStudentRole, IsTOTPEnrolled
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


def _github_oauth_html(title: str, message: str) -> str:
    """
    Self-contained confirmation page shown after the GitHub redirect when no
    GITHUB_OAUTH_SUCCESS/FAILURE_REDIRECT_URL is configured for the SPA to
    redirect back into. No external resources, so it's safe under any CSP.
    """
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{title}</title></head>"
        "<body style=\"font-family: sans-serif; text-align: center; padding: 4rem;\">"
        f"<h2>{title}</h2><p>{message}</p>"
        "<p>You can close this tab.</p></body></html>"
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


class GitHubOAuthConnectView(APIView):
    """
    GET /api/auth/github/connect/

    Starts the GitHub OAuth flow for the logged-in student. Returns a
    GitHub authorize URL the frontend should do a full browser redirect to
    (not fetch it -- GitHub's login/consent page can't be loaded via XHR).

    A one-time `state` tying this request to the current user is cached for
    a few minutes so /github/callback/ -- which arrives as a plain browser
    redirect from GitHub with no Authorization header -- can tell which
    student to attach the resulting token to, without trusting anything
    GitHub sends other than the code itself.
    """

    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsStudentRole]
    throttle_scope = "auth"

    def get(self, request):
        try:
            state = create_oauth_state(request.user.id)
            authorize_url = build_authorize_url(state)
        except GitHubOAuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response({"authorize_url": authorize_url}, status=status.HTTP_200_OK)


class GitHubOAuthCallbackView(APIView):
    """
    GET /api/auth/github/callback/

    GitHub redirects the student's browser here after consent. There is no
    JWT on this request, so identity comes solely from the cached `state`
    minted in GitHubOAuthConnectView -- never from anything GitHub passes
    other than the authorization `code`.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def get(self, request):
        error = request.query_params.get("error")
        state = request.query_params.get("state")
        code = request.query_params.get("code")

        if error:
            return self._failure(f"GitHub authorization was not completed ({error}).")

        if not state or not code:
            return self._failure("Missing state or code in GitHub's response.")

        user_id = consume_oauth_state(state)
        if not user_id:
            return self._failure("This connection request expired or was already used. Please try again.")

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return self._failure("Account no longer exists.")

        try:
            token_response = exchange_code_for_token(code)
            identity = fetch_github_identity(token_response["access_token"])
            connection = save_connection(user, token_response, identity)
        except GitHubOAuthError as exc:
            return self._failure(str(exc))

        return self._success(connection.github_username)

    def _success(self, github_username: str):
        redirect_url = os.getenv("GITHUB_OAUTH_SUCCESS_REDIRECT_URL")
        if redirect_url:
            return HttpResponseRedirect(f"{redirect_url}?github=connected&username={github_username}")

        return HttpResponse(
            _github_oauth_html("GitHub connected", f"Connected as @{github_username}."),
            status=status.HTTP_200_OK,
            content_type="text/html",
        )

    def _failure(self, reason: str):
        redirect_url = os.getenv("GITHUB_OAUTH_FAILURE_REDIRECT_URL")
        if redirect_url:
            return HttpResponseRedirect(f"{redirect_url}?github=error")

        return HttpResponse(
            _github_oauth_html("GitHub connection failed", reason),
            status=status.HTTP_400_BAD_REQUEST,
            content_type="text/html",
        )


class GitHubOAuthStatusView(APIView):
    """GET /api/auth/github/status/ -- never exposes the stored token itself."""

    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsStudentRole]

    def get(self, request):
        connection = get_connection_for_user(request.user)
        if connection is None:
            return Response({"connected": False}, status=status.HTTP_200_OK)

        return Response(
            {
                "connected": True,
                "github_username": connection.github_username,
                "connected_at": connection.connected_at,
            },
            status=status.HTTP_200_OK,
        )


class GitHubOAuthDisconnectView(APIView):
    """DELETE /api/auth/github/disconnect/"""

    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsStudentRole]

    def delete(self, request):
        connection = get_connection_for_user(request.user)
        if connection is None:
            return Response({"detail": "No GitHub connection to remove."}, status=status.HTTP_404_NOT_FOUND)

        revoke_and_delete(connection)
        return Response(status=status.HTTP_204_NO_CONTENT)
