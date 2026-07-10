from __future__ import annotations

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsTOTPEnrolled
from identity_verification.models import DeviceBiometricPreference
from identity_verification.permissions import IsIdentityStudent
from identity_verification.serializers import (
    DeviceBiometricPreferenceSerializer,
    IdentitySessionDetailSerializer,
    IdentitySessionSerializer,
)
from identity_verification.services import complete_identity_session, create_identity_session


class IdentitySessionCreateView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsIdentityStudent]
    throttle_scope = "identity"

    def post(self, request):
        session = create_identity_session(request.user.account)
        return Response(IdentitySessionSerializer(session).data, status=status.HTTP_201_CREATED)


class IdentitySessionDetailView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsIdentityStudent]
    throttle_scope = "identity"

    def get(self, request, session_id):
        try:
            session = request.user.account.identity_sessions.get(id=session_id)
        except Exception:  # noqa: BLE001
            return Response({"detail": "Verification session not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(IdentitySessionDetailSerializer(session).data, status=status.HTTP_200_OK)


class IdentitySessionCompleteView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsIdentityStudent]
    throttle_scope = "identity"

    def post(self, request, session_id):
        if request.content_type and not request.content_type.startswith("application/json"):
            return Response({"detail": "Only application/json is accepted."}, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        try:
            session, result, proof = complete_identity_session(
                account=request.user.account,
                session_id=session_id,
                payload=dict(request.data),
            )
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        response_proof = {
            "verification_record_hash": proof.verification_record_hash,
            "current_head": proof.current_head,
            "freshness_timestamp": proof.freshness_timestamp,
            "challenge": proof.challenge,
            "epoch": proof.signing_epoch,
            "signature": proof.signature,
        }
        return Response(
            {
                "session_id": str(session.id),
                "status": session.status,
                "liveness_result": result.final_liveness_result,
                "profile_status": "active_liveness_passed" if result.final_liveness_result == "passed" else result.final_liveness_result,
                "proof": response_proof,
            },
            status=status.HTTP_200_OK,
        )


class DeviceBiometricPreferenceView(APIView):
    parser_classes = [JSONParser]
    permission_classes = [IsAuthenticated, IsTOTPEnrolled, IsIdentityStudent]
    throttle_scope = "identity"

    def post(self, request):
        if request.content_type and not request.content_type.startswith("application/json"):
            return Response({"detail": "Only application/json is accepted."}, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        serializer = DeviceBiometricPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            preference, _ = DeviceBiometricPreference.objects.update_or_create(
                account=request.user.account,
                defaults=serializer.validated_data,
            )
        return Response(
            {
                "status": preference.status,
                "platform": preference.platform,
                "app_version": preference.app_version,
                "updated_at": preference.updated_at,
            },
            status=status.HTTP_200_OK,
        )
