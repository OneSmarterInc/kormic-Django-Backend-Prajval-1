from __future__ import annotations

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from accounts.models import Account
from django_api.models import StudentProfile
from django_api.services import make_student_id

# Same field set universities.views.UniversityProfileAPIView lets a
# university officer patch on their own profile, just reachable here for any
# university_id. Kept as a separate copy (not imported from universities.views)
# since that module's set is underscore-private and this app is meant to
# stay self-contained.
ADMIN_PATCHABLE_UNIVERSITY_FIELDS = {
    "name",
    "location",
    "tagline",
    "description",
    "contact_email",
    "contact_phone",
    "website_url",
    "admissions_office_address",
    "eligibility_criteria",
    "scrape_urls",
    "tone_descriptors",
    "best_fit_notes",
    "not_best_fit_notes",
    "communication_style_notes",
    "never_do_notes",
}

# Subset of the above that's also mirrored into the knowledge base --
# changing any of these should re-sync the derived KB facts.
KB_SYNCED_UNIVERSITY_FIELDS = {
    "description",
    "contact_email",
    "contact_phone",
    "website_url",
    "admissions_office_address",
    "eligibility_criteria",
}


class AdminCreateStudentSerializer(serializers.Serializer):
    """Superuser-driven equivalent of accounts.serializers.RegisterSerializer's
    student branch -- creates the login (User+Account) and a blank
    StudentProfile row in one call, without the student self-registering."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    name = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def validate(self, attrs):
        student_id = make_student_id(attrs["email"])
        if Account.objects.filter(student_id=student_id).exists():
            raise serializers.ValidationError(
                {"student_id": "An account derived from this email already exists."}
            )
        attrs["student_id"] = student_id
        return attrs

    def create(self, validated_data) -> User:
        email = validated_data["email"]
        name = validated_data.get("name", "")
        student_id = validated_data["student_id"]

        with transaction.atomic():
            user = User.objects.create_user(
                username=email,
                email=email,
                password=validated_data["password"],
                first_name=name[:150],
            )
            Account.objects.create(user=user, role=Account.Role.STUDENT, student_id=student_id)
            StudentProfile.objects.get_or_create(
                student_id=student_id,
                defaults={"name": name, "email": email},
            )

        return user


class AdminCreateSuperuserSerializer(serializers.Serializer):
    """Lets an existing superuser mint another one, so bootstrapping via the
    create_superuser_account management command only has to happen once per
    deployment."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    name = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def create(self, validated_data) -> User:
        email = validated_data["email"]

        with transaction.atomic():
            user = User.objects.create_user(
                username=email,
                email=email,
                password=validated_data["password"],
                first_name=validated_data.get("name", "")[:150],
            )
            Account.objects.create(user=user, role=Account.Role.SUPERUSER)

        return user


class AdminEnrollUniversitySerializer(serializers.Serializer):
    """Registers a new University (mirrors universities.services.register_university)
    and, optionally, the officer login that manages it -- one call instead of
    the two-step register_university + /api/auth/register/ flow a university
    would normally do for itself."""

    institution_name = serializers.CharField()
    profile = serializers.DictField(required=False, default=dict)
    officer_email = serializers.EmailField(required=False, allow_blank=True, default="")
    officer_password = serializers.CharField(required=False, allow_blank=True, write_only=True, default="")
    officer_name = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_institution_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("institution_name cannot be blank.")
        return value

    def validate(self, attrs):
        email = attrs.get("officer_email", "").strip()
        password = attrs.get("officer_password", "")

        if bool(email) != bool(password):
            raise serializers.ValidationError(
                {"officer_email": "officer_email and officer_password must be provided together."}
            )

        if email:
            if User.objects.filter(email__iexact=email).exists():
                raise serializers.ValidationError({"officer_email": "An account with this email already exists."})
            validate_password(password)

        return attrs

    def create(self, validated_data):
        # Lazy import: mirrors accounts.serializers.RegisterSerializer, which
        # imports universities.models/services inside validate()/create()
        # rather than at module level for the same reason.
        from universities.services import register_university, sync_profile_facts_to_kb

        with transaction.atomic():
            university = register_university(validated_data["institution_name"])

            profile = validated_data.get("profile") or {}
            changed_kb_fields = False
            touched = False
            for field in ADMIN_PATCHABLE_UNIVERSITY_FIELDS:
                if field not in profile:
                    continue
                setattr(university, field, profile[field])
                touched = True
                if field in KB_SYNCED_UNIVERSITY_FIELDS:
                    changed_kb_fields = True

            if touched:
                university.save()
            if changed_kb_fields:
                sync_profile_facts_to_kb(university)

            officer_email = validated_data.get("officer_email", "").strip()
            if officer_email:
                officer = User.objects.create_user(
                    username=officer_email,
                    email=officer_email,
                    password=validated_data["officer_password"],
                    first_name=validated_data.get("officer_name", "")[:150],
                )
                Account.objects.create(user=officer, role=Account.Role.UNIVERSITY, university_id=university.id)

        return university
