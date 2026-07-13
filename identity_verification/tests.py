from __future__ import annotations

from copy import deepcopy

import pyotp
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from django_api.models import StudentProfile
from identity_verification.crypto import aes_gcm
from identity_verification.crypto.canonical import canonical_bytes, canonical_json
from identity_verification.crypto.custody import SoftwareKeyCustody
from identity_verification.crypto.proof_tokens import build_current_head, sha3_256_hex, sign_proof_record, verify_proof_record
from identity_verification.models import (
    DeviceBiometricPreference,
    IdentityVerificationProof,
    IdentityVerificationResult,
    IdentityVerificationSession,
)
from identity_verification.services import CHALLENGE_SEQUENCE


PASSWORD = "S3curePassw0rd!"


def register_and_enroll(client, *, email, student_id):
    client.post(
        "/api/auth/register/",
        {"email": email, "password": PASSWORD, "role": "student", "student_id": student_id, "name": "Test Student"},
        format="json",
    )
    access = client.post("/api/auth/login/", {"email": email, "password": PASSWORD}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    secret = client.post("/api/auth/totp/enroll/").data["secret"]
    client.post("/api/auth/totp/verify-enrollment/", {"code": pyotp.TOTP(secret).now()}, format="json")
    client.credentials()
    mfa_token = client.post("/api/auth/login/", {"email": email, "password": PASSWORD}, format="json").data["mfa_token"]
    tokens = client.post("/api/auth/verify-totp/", {"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()}, format="json").data
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


def make_student(email="student@example.com", student_id="student_a"):
    return register_and_enroll(APIClient(), email=email, student_id=student_id)


def valid_completion_payload(session: IdentityVerificationSession, *, final="passed"):
    started = session.created_at + timezone.timedelta(seconds=5)
    return {
        "session_nonce": session.session_nonce,
        "challenge_results": [
            {"challenge": challenge, "passed": True, "completed_at": (started + timezone.timedelta(seconds=index + 1)).isoformat()}
            for index, challenge in enumerate(session.challenge_sequence)
        ],
        "started_at": started.isoformat(),
        "completed_at": (started + timezone.timedelta(seconds=10)).isoformat(),
        "detector_provider": "vision-camera-face-detector-2.0.6",
        "platform": "ios",
        "app_version": "1.0.0",
        "final_liveness_result": final,
        "failure_reason": None,
    }


@override_settings(IDENTITY_ALLOW_DEV_KEY_CUSTODY=True, IDENTITY_CURRENT_SIGNING_EPOCH=1)
class IdentityVerificationApiTests(TestCase):
    def setUp(self):
        cache.clear()
        SoftwareKeyCustody._keys.clear()
        self.student = make_student(email="a@example.com", student_id="student_a")
        self.other_student = make_student(email="b@example.com", student_id="student_b")

    def create_session(self, client=None):
        client = client or self.student
        response = client.post("/api/identity/sessions/", {"student_id": "evil"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response, IdentityVerificationSession.objects.get(id=response.data["session_id"])

    def test_unauthenticated_rejected(self):
        response = APIClient().post("/api/identity/sessions/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_student_can_create_session_with_secure_id_and_nonce(self):
        response, session = self.create_session()
        self.assertEqual(response.data["challenge_sequence"], CHALLENGE_SEQUENCE)
        self.assertEqual(response.data["status"], "created")
        self.assertEqual(session.student_code, "student_a")
        self.assertGreaterEqual(len(response.data["session_nonce"]), 32)
        self.assertNotEqual(str(session.id), response.data["session_nonce"])

    def test_client_student_id_is_ignored(self):
        _, session = self.create_session()
        self.assertEqual(session.student_code, "student_a")
        self.assertFalse(IdentityVerificationSession.objects.filter(account__student_id="evil").exists())

    def test_another_student_cannot_read_or_complete_session(self):
        _, session = self.create_session()
        detail = self.other_student.get(f"/api/identity/sessions/{session.id}/")
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)
        complete = self.other_student.post(f"/api/identity/sessions/{session.id}/complete/", valid_completion_payload(session), format="json")
        self.assertEqual(complete.status_code, status.HTTP_404_NOT_FOUND)

    def test_exact_challenge_order_accepted_and_profile_not_marked_verified(self):
        StudentProfile.objects.create(student_id="student_a", verified=False)
        _, session = self.create_session()
        response = self.student.post(f"/api/identity/sessions/{session.id}/complete/", valid_completion_payload(session), format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["profile_status"], "active_liveness_passed")
        session.refresh_from_db()
        self.assertEqual(session.status, IdentityVerificationSession.Status.COMPLETED)
        self.assertIsNotNone(session.consumed_at)
        result = session.result
        self.assertEqual(result.final_liveness_result, "passed")
        self.assertEqual(set(result.encrypted_payload.keys()), {"nonce", "ciphertext", "tag"})
        self.assertEqual(IdentityVerificationProof.objects.filter(session=session).count(), 1)
        self.assertFalse(StudentProfile.objects.get(student_id="student_a").verified)

    def test_duplicate_completion_rejected(self):
        _, session = self.create_session()
        payload = valid_completion_payload(session)
        self.assertEqual(self.student.post(f"/api/identity/sessions/{session.id}/complete/", payload, format="json").status_code, 200)
        response = self.student.post(f"/api/identity/sessions/{session.id}/complete/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(IdentityVerificationResult.objects.filter(session=session).count(), 1)

    def test_expired_completion_rejected_and_status_persisted(self):
        _, session = self.create_session()
        session.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        session.save(update_fields=["expires_at"])
        response = self.student.post(f"/api/identity/sessions/{session.id}/complete/", valid_completion_payload(session), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        session.refresh_from_db()
        self.assertEqual(session.status, IdentityVerificationSession.Status.EXPIRED)

    def test_failed_result_is_terminal_and_cannot_become_passed(self):
        _, session = self.create_session()
        payload = valid_completion_payload(session, final="failed")
        payload["challenge_results"][0]["passed"] = False
        payload["failure_reason"] = "challenge_failed"
        response = self.student.post(f"/api/identity/sessions/{session.id}/complete/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "failed")
        replay = self.student.post(f"/api/identity/sessions/{session.id}/complete/", valid_completion_payload(session), format="json")
        self.assertEqual(replay.status_code, status.HTTP_400_BAD_REQUEST)
        session.refresh_from_db()
        self.assertEqual(session.status, IdentityVerificationSession.Status.FAILED)

    def test_canceled_session_rejected(self):
        _, session = self.create_session()
        session.status = IdentityVerificationSession.Status.CANCELED
        session.save(update_fields=["status"])
        response = self.student.post(f"/api/identity/sessions/{session.id}/complete/", valid_completion_payload(session), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_wrong_missing_duplicate_unknown_challenges_rejected(self):
        _, session = self.create_session()
        cases = []
        wrong = valid_completion_payload(session)
        wrong["challenge_results"] = list(reversed(wrong["challenge_results"]))
        cases.append(wrong)
        missing = valid_completion_payload(session)
        missing["challenge_results"] = missing["challenge_results"][:-1]
        cases.append(missing)
        duplicate = valid_completion_payload(session)
        duplicate["challenge_results"][1] = deepcopy(duplicate["challenge_results"][0])
        cases.append(duplicate)
        unknown = valid_completion_payload(session)
        unknown["challenge_results"][0]["challenge"] = "smile"
        cases.append(unknown)
        for payload in cases:
            response = self.student.post(f"/api/identity/sessions/{session.id}/complete/", payload, format="json")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_passed_result_requires_every_challenge_passed(self):
        _, session = self.create_session()
        payload = valid_completion_payload(session)
        payload["challenge_results"][2]["passed"] = False
        response = self.student.post(f"/api/identity/sessions/{session.id}/complete/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_timestamp_order_and_nonce_mismatch_rejected(self):
        _, session = self.create_session()
        payload = valid_completion_payload(session)
        payload["session_nonce"] = "wrong"
        response = self.student.post(f"/api/identity/sessions/{session.id}/complete/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("session_nonce", response.data)
        payload = valid_completion_payload(session)
        payload["completed_at"] = (session.created_at - timezone.timedelta(seconds=1)).isoformat()
        self.assertEqual(self.student.post(f"/api/identity/sessions/{session.id}/complete/", payload, format="json").status_code, 400)

    def test_media_and_multipart_rejected(self):
        _, session = self.create_session()
        payload = valid_completion_payload(session)
        payload["frames"] = ["not accepted"]
        self.assertEqual(self.student.post(f"/api/identity/sessions/{session.id}/complete/", payload, format="json").status_code, 400)
        file_response = self.student.post(
            f"/api/identity/sessions/{session.id}/complete/",
            {"file": SimpleUploadedFile("face.jpg", b"raw")},
            format="multipart",
        )
        self.assertEqual(file_response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        create_multipart = self.student.post(
            "/api/identity/sessions/",
            {"file": SimpleUploadedFile("face.jpg", b"raw")},
            format="multipart",
        )
        self.assertEqual(create_multipart.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_oversized_unexpected_field_rejected(self):
        _, session = self.create_session()
        payload = valid_completion_payload(session)
        payload["app_version"] = "x" * 200
        response = self.student.post(f"/api/identity/sessions/{session.id}/complete/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_device_biometric_preference_accepts_status_only(self):
        response = self.student.post(
            "/api/identity/device-biometrics/",
            {"status": "enabled", "platform": "ios", "app_version": "1.0.0"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(DeviceBiometricPreference.objects.get(account__student_id="student_a").status, "enabled")
        updated = self.student.post(
            "/api/identity/device-biometrics/",
            {"status": "skipped", "platform": "ios", "app_version": "1.0.1"},
            format="json",
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.assertEqual(DeviceBiometricPreference.objects.get(account__student_id="student_a").status, "skipped")
        self.assertEqual(DeviceBiometricPreference.objects.filter(account__student_id="student_a").count(), 1)
        bad = self.student.post(
            "/api/identity/device-biometrics/",
            {"status": "enabled", "platform": "ios", "app_version": "1.0.0", "face_id_template": "secret"},
            format="json",
        )
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(StudentProfile.objects.filter(student_id="student_a", verified=True).exists())

    def test_one_proof_per_result_database_constraint(self):
        _, session = self.create_session()
        response = self.student.post(f"/api/identity/sessions/{session.id}/complete/", valid_completion_payload(session), format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = session.result
        existing = result.proof
        with self.assertRaises(IntegrityError):
            IdentityVerificationProof.objects.create(
                session=session,
                result=result,
                student_code=existing.student_code,
                verification_record_hash=existing.verification_record_hash,
                current_head=existing.current_head,
                freshness_timestamp=existing.freshness_timestamp,
                challenge=existing.challenge,
                signing_epoch=existing.signing_epoch,
                signature=existing.signature,
                authority_identifier=existing.authority_identifier,
            )


@override_settings(IDENTITY_ALLOW_DEV_KEY_CUSTODY=True, IDENTITY_CURRENT_SIGNING_EPOCH=1)
class IdentityCryptoTests(TestCase):
    def setUp(self):
        SoftwareKeyCustody._keys.clear()

    def test_aes_gcm_round_trip_and_tamper_rejection(self):
        aad = b"session-aad"
        envelope = aes_gcm.encrypt(b"sensitive metadata", associated_data=aad)
        self.assertEqual(aes_gcm.decrypt(envelope, associated_data=aad), b"sensitive metadata")
        envelope2 = aes_gcm.encrypt(b"sensitive metadata", associated_data=aad)
        self.assertNotEqual(envelope["nonce"], envelope2["nonce"])
        tampered_ciphertext = dict(envelope)
        tampered_ciphertext["ciphertext"] = envelope2["ciphertext"]
        with self.assertRaises(ValueError):
            aes_gcm.decrypt(tampered_ciphertext, associated_data=aad)
        tampered_tag = dict(envelope)
        tampered_tag["tag"] = envelope2["tag"]
        with self.assertRaises(ValueError):
            aes_gcm.decrypt(tampered_tag, associated_data=aad)

    def test_canonicalization_is_deterministic(self):
        left = {"b": 2, "a": {"d": 4, "c": 3}}
        right = {"a": {"c": 3, "d": 4}, "b": 2}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(canonical_bytes(left), b'{"a":{"c":3,"d":4},"b":2}')

    def test_mldsa_sign_verify_and_altered_payload_fails(self):
        proof = sign_proof_record(
            student_code="student_a",
            verification_record_hash="abc",
            current_head="def",
            challenge=",".join(CHALLENGE_SEQUENCE),
        )
        self.assertEqual(proof["epoch"], 1)
        self.assertTrue(verify_proof_record(proof))
        altered = dict(proof)
        altered["current_head"] = "changed"
        self.assertFalse(verify_proof_record(altered))

    @override_settings(IDENTITY_CURRENT_SIGNING_EPOCH=7)
    def test_old_epoch_remains_verifiable_after_rotation(self):
        proof = sign_proof_record(student_code="student_a", verification_record_hash="abc", current_head="head", challenge="challenge")
        self.assertEqual(proof["epoch"], 7)
        with self.settings(IDENTITY_CURRENT_SIGNING_EPOCH=8):
            newer = sign_proof_record(student_code="student_a", verification_record_hash="next", current_head="head2", challenge="challenge")
            self.assertEqual(newer["epoch"], 8)
            self.assertTrue(verify_proof_record(proof))
            self.assertTrue(verify_proof_record(newer))

    @override_settings(DEBUG=False, IDENTITY_ALLOW_DEV_KEY_CUSTODY=False)
    def test_production_cannot_use_dev_custody(self):
        with self.assertRaises(RuntimeError):
            SoftwareKeyCustody()

    def test_private_key_not_stored_in_models_and_chain_head_deterministic(self):
        field_names = {field.name for field in IdentityVerificationProof._meta.fields} | {field.name for field in IdentityVerificationSession._meta.fields}
        self.assertNotIn("private_key", field_names)
        self.assertEqual(
            build_current_head("prev", "hash", "session"),
            sha3_256_hex(canonical_bytes({"previous_head": "prev", "record_hash": "hash", "session_id": "session"})),
        )
