from __future__ import annotations

import secrets
import uuid

from django.db import models

from accounts.models import Account


def generate_session_nonce() -> str:
    return secrets.token_urlsafe(32)


class IdentityVerificationSession(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Created"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="identity_sessions")
    session_nonce = models.CharField(max_length=128, default=generate_session_nonce, editable=False)
    challenge_sequence = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    previous_head = models.CharField(max_length=128, blank=True, default="")
    current_head = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["account", "status"])]

    @property
    def student_code(self) -> str:
        return self.account.student_id or ""

    def __str__(self) -> str:
        return f"IdentityVerificationSession({self.id}, {self.student_code}, {self.status})"


class IdentityVerificationResult(models.Model):
    class FinalResult(models.TextChoices):
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    session = models.OneToOneField(IdentityVerificationSession, on_delete=models.CASCADE, related_name="result")
    encrypted_payload = models.JSONField()
    detector_provider = models.CharField(max_length=120)
    platform = models.CharField(max_length=20)
    app_version = models.CharField(max_length=80)
    final_liveness_result = models.CharField(max_length=20, choices=FinalResult.choices)
    failure_reason = models.CharField(max_length=120, blank=True, default="")
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    payload_hash = models.CharField(max_length=128, unique=True, db_index=True)

    class Meta:
        ordering = ["-received_at"]

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.pop("allow_update", False):
            raise RuntimeError("IdentityVerificationResult records are immutable.")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"IdentityVerificationResult({self.session_id}, {self.final_liveness_result})"


class IdentityVerificationProof(models.Model):
    session = models.ForeignKey(IdentityVerificationSession, on_delete=models.CASCADE, related_name="proofs")
    result = models.OneToOneField(IdentityVerificationResult, on_delete=models.CASCADE, related_name="proof")
    student_code = models.CharField(max_length=255, db_index=True)
    verification_record_hash = models.CharField(max_length=128, db_index=True)
    current_head = models.CharField(max_length=128, db_index=True)
    freshness_timestamp = models.DateTimeField()
    challenge = models.CharField(max_length=255)
    signing_epoch = models.PositiveIntegerField()
    signature = models.TextField()
    authority_identifier = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.pop("allow_update", False):
            raise RuntimeError("IdentityVerificationProof records are append-only.")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"IdentityVerificationProof({self.session_id}, epoch={self.signing_epoch})"


class DeviceBiometricPreference(models.Model):
    class Status(models.TextChoices):
        ENABLED = "enabled", "Enabled"
        SKIPPED = "skipped", "Skipped"
        UNAVAILABLE = "unavailable", "Unavailable"

    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="device_biometric_preference")
    status = models.CharField(max_length=20, choices=Status.choices)
    platform = models.CharField(max_length=20)
    app_version = models.CharField(max_length=80)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"DeviceBiometricPreference({self.account_id}, {self.status})"


