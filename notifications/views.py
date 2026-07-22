from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsTOTPEnrolled, get_account
from notifications.models import PushToken


class RegisterPushTokenView(APIView):
    """
    POST /api/notifications/register-token/
    Body: {"token": "ExponentPushToken[...]", "platform": "ios" | "android"}

    Called by the Expo app right after it obtains its push token (typically
    on login and on app start). Re-registering an existing token reassigns
    it to the current account -- correct behavior when a different student
    logs in on a previously-used device.
    """

    permission_classes = [IsAuthenticated, IsTOTPEnrolled]

    def post(self, request):
        token = (request.data.get("token") or "").strip()
        if not token:
            return Response({"error": "token is required."}, status=status.HTTP_400_BAD_REQUEST)

        platform = request.data.get("platform", PushToken.Platform.UNKNOWN)
        if platform not in PushToken.Platform.values:
            platform = PushToken.Platform.UNKNOWN

        account = get_account(request)
        if account is None:
            return Response({"error": "No account associated with this user."}, status=status.HTTP_403_FORBIDDEN)

        push_token, _ = PushToken.objects.update_or_create(
            token=token,
            defaults={"account": account, "platform": platform, "is_active": True, "last_error": ""},
        )
        return Response(
            {"id": push_token.id, "token": push_token.token, "platform": push_token.platform},
            status=status.HTTP_200_OK,
        )


class UnregisterPushTokenView(APIView):
    """
    POST /api/notifications/unregister-token/
    Body: {"token": "ExponentPushToken[...]"}

    Called on logout / notification permission revocation so a signed-out
    device stops receiving pushes for the account that registered it.
    """

    permission_classes = [IsAuthenticated, IsTOTPEnrolled]

    def post(self, request):
        token = (request.data.get("token") or "").strip()
        if not token:
            return Response({"error": "token is required."}, status=status.HTTP_400_BAD_REQUEST)

        account = get_account(request)
        updated = PushToken.objects.filter(token=token, account=account).update(is_active=False)
        return Response({"deactivated": bool(updated)}, status=status.HTTP_200_OK)
