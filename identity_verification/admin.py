from django.contrib import admin

from identity_verification.models import (
    DeviceBiometricPreference,
    IdentityVerificationProof,
    IdentityVerificationResult,
    IdentityVerificationSession,
)


@admin.register(IdentityVerificationSession)
class IdentityVerificationSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "account", "status", "expires_at", "consumed_at", "attempt_count")
    search_fields = ("id", "account__student_id", "account__user__email")
    list_filter = ("status",)
    readonly_fields = ("id", "session_nonce", "challenge_sequence", "current_head", "previous_head")


@admin.register(IdentityVerificationResult)
class IdentityVerificationResultAdmin(admin.ModelAdmin):
    list_display = ("session", "final_liveness_result", "detector_provider", "platform", "received_at")
    search_fields = ("session__id", "session__account__student_id", "payload_hash")
    readonly_fields = ("encrypted_payload", "payload_hash", "received_at")


@admin.register(IdentityVerificationProof)
class IdentityVerificationProofAdmin(admin.ModelAdmin):
    list_display = ("session", "student_code", "signing_epoch", "authority_identifier", "created_at")
    search_fields = ("session__id", "student_code", "verification_record_hash", "current_head")
    readonly_fields = ("signature", "verification_record_hash", "current_head")


@admin.register(DeviceBiometricPreference)
class DeviceBiometricPreferenceAdmin(admin.ModelAdmin):
    list_display = ("account", "status", "platform", "app_version", "updated_at")
    search_fields = ("account__student_id", "account__user__email")
    list_filter = ("status", "platform")
