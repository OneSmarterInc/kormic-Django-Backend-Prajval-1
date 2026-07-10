from __future__ import annotations

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from accounts.models import Account
from django_api.services import make_student_id
from personas.university_personas import UNIVERSITY_PERSONAS


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(choices=Account.Role.choices)
    name = serializers.CharField(required=False, allow_blank=True, default="")

    # role=student
    student_id = serializers.CharField(required=False, allow_blank=True)
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
            raw_student_id = attrs.get("student_id") or attrs.get("email")
            student_id = make_student_id(raw_student_id)
            if Account.objects.filter(student_id=student_id).exists():
                raise serializers.ValidationError({"student_id": "This student_id is already registered."})
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


def serialize_user(user: User) -> dict:
    account = getattr(user, "account", None)
    totp_enrolled = hasattr(user, "totp_device") and user.totp_device.confirmed_at is not None

    return {
        "id": user.id,
        "email": user.email,
        "name": user.first_name,
        "role": account.role if account else None,
        "student_id": account.student_id if account else None,
        "university_id": account.university_id if account else None,
        "totp_enrolled": totp_enrolled,
    }
