from __future__ import annotations

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from accounts.models import Account
from django_api.models import LinkedInAnalysis, ResumeUpload, StudentProfile
from django_api.services import make_student_id
from personas.university_personas import UNIVERSITY_PERSONAS


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(choices=Account.Role.choices)
    name = serializers.CharField(required=False, allow_blank=True, default="")

    # role=university
    university_id = serializers.CharField(required=False, allow_blank=True)

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def validate(self, attrs):
        role = attrs["role"]

        if role == Account.Role.STUDENT:
            student_id = make_student_id(attrs["email"])
            if Account.objects.filter(student_id=student_id).exists():
                raise serializers.ValidationError(
                    {"student_id": "An account derived from this email already exists."}
                )
            attrs["student_id"] = student_id

        elif role == Account.Role.UNIVERSITY:
            university_id = attrs.get("university_id")
            if not university_id or university_id not in UNIVERSITY_PERSONAS:
                raise serializers.ValidationError({
                    "university_id": (
                        f"Unknown university_id. Must be one of: {', '.join(UNIVERSITY_PERSONAS.keys())}"
                    )
                })

        return attrs

    def create(self, validated_data) -> User:
        email = validated_data["email"]
        user = User.objects.create_user(
            username=email,
            email=email,
            password=validated_data["password"],
            first_name=validated_data.get("name", "")[:150],
        )

        Account.objects.create(
            user=user,
            role=validated_data["role"],
            student_id=validated_data.get("student_id"),
            university_id=validated_data.get("university_id") or None,
        )

        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class VerifyTOTPSerializer(serializers.Serializer):
    mfa_token = serializers.CharField()
    code = serializers.CharField()


class EnrollVerifySerializer(serializers.Serializer):
    code = serializers.CharField()


def student_onboarding_status(student_id: str) -> dict:
    """
    Derived (not stored) so it can never drift out of sync with the actual
    data: a student's "already provided this" state is just whatever is in
    the DB right now, not a separately-tracked wizard-completion flag.
    """
    profile = StudentProfile.objects.filter(student_id=student_id).first()
    resume_uploaded = ResumeUpload.objects.filter(student__student_id=student_id).exists()
    github_connected = bool(profile and profile.github)
    # LinkedIn is normally captured via image upload + parsing (not a typed
    # URL), so `profile.linkedin_url` alone stays empty for that path --
    # LinkedInAnalysis rows are the reliable signal. A manually-typed
    # linkedin_url (via the plain profile-update endpoint) also counts.
    linkedin_connected = bool(profile and profile.linkedin_url) or LinkedInAnalysis.objects.filter(
        student__student_id=student_id
    ).exists()

    return {
        "profile_exists": profile is not None,
        "resume_uploaded": resume_uploaded,
        "github_connected": github_connected,
        "linkedin_connected": linkedin_connected,
        "setup_complete": resume_uploaded and github_connected and linkedin_connected,
    }


def serialize_user(user: User) -> dict:
    account = getattr(user, "account", None)
    totp_enrolled = hasattr(user, "totp_device") and user.totp_device.confirmed_at is not None

    data = {
        "id": user.id,
        "email": user.email,
        "name": user.first_name,
        "role": account.role if account else None,
        "student_id": account.student_id if account else None,
        "university_id": account.university_id if account else None,
        "totp_enrolled": totp_enrolled,
    }

    if account and account.role == Account.Role.STUDENT and account.student_id:
        data["onboarding"] = student_onboarding_status(account.student_id)

    return data
