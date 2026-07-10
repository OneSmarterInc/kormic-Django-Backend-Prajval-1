from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from accounts.models import Account
from identity_verification.crypto import aes_gcm
from identity_verification.crypto.canonical import canonical_bytes
from identity_verification.crypto.proof_tokens import build_current_head, sha3_256_hex, sign_proof_record
from identity_verification.models import IdentityVerificationProof, IdentityVerificationResult, IdentityVerificationSession
from identity_verification.serializers import IdentityCompletionSerializer

CHALLENGE_SEQUENCE = ["center_face", "turn_left", "turn_right", "blink", "hold_still"]


def create_identity_session(account: Account) -> IdentityVerificationSession:
    ttl_seconds = int(getattr(settings, "IDENTITY_SESSION_TTL_SECONDS", 300))
    return IdentityVerificationSession.objects.create(
        account=account,
        challenge_sequence=list(CHALLENGE_SEQUENCE),
        expires_at=timezone.now() + timezone.timedelta(seconds=ttl_seconds),
    )


def complete_identity_session(*, account: Account, session_id, payload: dict) -> tuple[IdentityVerificationSession, IdentityVerificationResult, IdentityVerificationProof]:
    try:
        precheck_session = IdentityVerificationSession.objects.get(id=session_id, account=account)
    except IdentityVerificationSession.DoesNotExist as exc:
        raise NotFound("Verification session not found.") from exc

    if precheck_session.expires_at <= timezone.now() and not precheck_session.consumed_at:
        IdentityVerificationSession.objects.filter(id=precheck_session.id).update(status=IdentityVerificationSession.Status.EXPIRED)
        raise serializers.ValidationError({"session": "Verification session has expired."})

    with transaction.atomic():
        try:
            session = IdentityVerificationSession.objects.select_for_update().get(id=session_id, account=account)
        except IdentityVerificationSession.DoesNotExist as exc:
            raise NotFound("Verification session not found.") from exc

        session.attempt_count += 1
        session.save(update_fields=["attempt_count"])

        serializer = IdentityCompletionSerializer(data=payload, context={"session": session})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        canonical_payload = {
            "session_id": str(session.id),
            "student_code": session.student_code,
            "challenge_sequence": session.challenge_sequence,
            "challenge_results": [
                {
                    "challenge": item["challenge"],
                    "passed": item["passed"],
                    "completed_at": item["completed_at"].isoformat(),
                }
                for item in data["challenge_results"]
            ],
            "started_at": data["started_at"].isoformat(),
            "completed_at": data["completed_at"].isoformat(),
            "detector_provider": data["detector_provider"],
            "platform": data["platform"],
            "app_version": data["app_version"],
            "final_liveness_result": data["final_liveness_result"],
            "failure_reason": data.get("failure_reason") or "",
        }
        plaintext = canonical_bytes(canonical_payload)
        payload_hash = sha3_256_hex(plaintext)
        associated_data = canonical_bytes({"session_id": str(session.id), "student_code": session.student_code})
        encrypted_payload = aes_gcm.encrypt(plaintext, associated_data=associated_data)
        previous_head = session.current_head or session.previous_head or ""
        current_head = build_current_head(previous_head, payload_hash, str(session.id))

        result = IdentityVerificationResult.objects.create(
            session=session,
            encrypted_payload=encrypted_payload,
            detector_provider=data["detector_provider"],
            platform=data["platform"],
            app_version=data["app_version"],
            final_liveness_result=data["final_liveness_result"],
            failure_reason=data.get("failure_reason") or "",
            started_at=data["started_at"],
            completed_at=data["completed_at"],
            payload_hash=payload_hash,
        )

        proof_payload = sign_proof_record(
            student_code=session.student_code,
            verification_record_hash=payload_hash,
            current_head=current_head,
            challenge=",".join(session.challenge_sequence),
        )
        proof = IdentityVerificationProof.objects.create(
            session=session,
            result=result,
            student_code=proof_payload["student_code"],
            verification_record_hash=proof_payload["verification_record_hash"],
            current_head=proof_payload["current_head"],
            freshness_timestamp=proof_payload["freshness_timestamp"],
            challenge=proof_payload["challenge"],
            signing_epoch=proof_payload["epoch"],
            signature=proof_payload["signature"],
            authority_identifier=proof_payload["authority_identifier"],
        )

        final = data["final_liveness_result"]
        if final == IdentityVerificationResult.FinalResult.PASSED:
            session.status = IdentityVerificationSession.Status.COMPLETED
        elif final == IdentityVerificationResult.FinalResult.CANCELED:
            session.status = IdentityVerificationSession.Status.CANCELED
        else:
            session.status = IdentityVerificationSession.Status.FAILED
        session.started_at = data["started_at"]
        session.completed_at = data["completed_at"]
        session.consumed_at = timezone.now()
        session.previous_head = previous_head
        session.current_head = current_head
        session.save(update_fields=["status", "started_at", "completed_at", "consumed_at", "previous_head", "current_head"])

        return session, result, proof
