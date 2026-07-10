from __future__ import annotations

import hmac
from typing import Any, Dict, List

from django.utils import timezone
from rest_framework import serializers

from identity_verification.models import DeviceBiometricPreference, IdentityVerificationResult, IdentityVerificationSession

ALLOWED_CHALLENGES = ["center_face", "turn_left", "turn_right", "blink", "hold_still"]
BANNED_FIELDS = {
    "file", "files", "image", "images", "photo", "photos", "video", "videos", "frame", "frames",
    "landmark", "landmarks", "face_geometry", "eye_open_probability", "eye_probabilities",
    "biometric_template", "biometric_identifier", "face_id_template", "device_biometric_id",
    "raw_detection", "raw_detections", "face", "faces",
}


class IdentitySessionSerializer(serializers.ModelSerializer):
    session_id = serializers.UUIDField(source="id", read_only=True)

    class Meta:
        model = IdentityVerificationSession
        fields = ["session_id", "expires_at", "challenge_sequence", "session_nonce", "status"]


class IdentitySessionDetailSerializer(serializers.ModelSerializer):
    session_id = serializers.UUIDField(source="id", read_only=True)

    class Meta:
        model = IdentityVerificationSession
        fields = ["session_id", "expires_at", "challenge_sequence", "status", "started_at", "completed_at"]


class ChallengeResultSerializer(serializers.Serializer):
    challenge = serializers.ChoiceField(choices=ALLOWED_CHALLENGES)
    passed = serializers.BooleanField()
    completed_at = serializers.DateTimeField()

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unexpected = set(data.keys()) - {"challenge", "passed", "completed_at"}
            banned = sorted(set(data.keys()) & BANNED_FIELDS)
            if banned:
                raise serializers.ValidationError({"biometric_payload": f"Raw biometric/media fields are not accepted: {', '.join(banned)}"})
            if unexpected:
                raise serializers.ValidationError({"unexpected_fields": sorted(unexpected)})
        return super().to_internal_value(data)


class IdentityCompletionSerializer(serializers.Serializer):
    session_nonce = serializers.CharField(max_length=128)
    challenge_results = ChallengeResultSerializer(many=True)
    started_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField()
    detector_provider = serializers.CharField(max_length=120)
    platform = serializers.ChoiceField(choices=["ios", "android"])
    app_version = serializers.CharField(max_length=80)
    final_liveness_result = serializers.ChoiceField(choices=IdentityVerificationResult.FinalResult.choices)
    failure_reason = serializers.CharField(max_length=120, allow_blank=True, allow_null=True, required=False)

    def validate(self, attrs):
        unexpected = set(self.initial_data.keys()) - set(self.fields.keys())
        banned = sorted((set(self.initial_data.keys()) | unexpected) & BANNED_FIELDS)
        if banned:
            raise serializers.ValidationError({"biometric_payload": f"Raw biometric/media fields are not accepted: {', '.join(banned)}"})
        if unexpected:
            raise serializers.ValidationError({"unexpected_fields": sorted(unexpected)})

        session: IdentityVerificationSession = self.context["session"]
        now = timezone.now()
        if not hmac.compare_digest(attrs["session_nonce"], session.session_nonce):
            raise serializers.ValidationError({"session_nonce": "Invalid session nonce."})
        if session.expires_at <= now:
            raise serializers.ValidationError({"session": "Verification session has expired."})
        terminal_states = [
            IdentityVerificationSession.Status.COMPLETED,
            IdentityVerificationSession.Status.FAILED,
            IdentityVerificationSession.Status.CANCELED,
            IdentityVerificationSession.Status.EXPIRED,
        ]
        if session.consumed_at or session.status in terminal_states:
            raise serializers.ValidationError({"session": "Verification session is no longer completable."})

        started_at = attrs["started_at"]
        completed_at = attrs["completed_at"]
        if completed_at < started_at:
            raise serializers.ValidationError({"completed_at": "Completion time must be after start time."})
        if started_at < session.created_at:
            raise serializers.ValidationError({"started_at": "Start time must be within the issued session window."})
        if completed_at > now + timezone.timedelta(minutes=1):
            raise serializers.ValidationError({"completed_at": "Completion time is outside the acceptable clock window."})
        if completed_at > session.expires_at:
            raise serializers.ValidationError({"completed_at": "Completion time must be before the session expires."})

        results: List[Dict[str, Any]] = attrs["challenge_results"]
        challenges = [item["challenge"] for item in results]
        if challenges != session.challenge_sequence:
            raise serializers.ValidationError({"challenge_results": "Challenge results must exactly match the issued challenge sequence."})
        if len(challenges) != len(set(challenges)):
            raise serializers.ValidationError({"challenge_results": "Duplicate challenge results are not accepted."})
        if sorted(challenges) != sorted(ALLOWED_CHALLENGES):
            raise serializers.ValidationError({"challenge_results": "All issued challenges are required."})
        if attrs["final_liveness_result"] == IdentityVerificationResult.FinalResult.PASSED:
            failed = [item["challenge"] for item in results if not item["passed"]]
            if failed:
                raise serializers.ValidationError({"final_liveness_result": "Every challenge must pass for a passed liveness result."})
        return attrs


class DeviceBiometricPreferenceSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=DeviceBiometricPreference.Status.choices)
    platform = serializers.ChoiceField(choices=["ios", "android"])
    app_version = serializers.CharField(max_length=80)

    def validate(self, attrs):
        unexpected = set(self.initial_data.keys()) - set(self.fields.keys())
        banned = sorted((set(self.initial_data.keys()) | unexpected) & BANNED_FIELDS)
        if banned:
            raise serializers.ValidationError({"biometric_payload": f"Biometric data fields are not accepted: {', '.join(banned)}"})
        if unexpected:
            raise serializers.ValidationError({"unexpected_fields": sorted(unexpected)})
        return attrs

