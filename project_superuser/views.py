from __future__ import annotations

from typing import Any, Dict

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.db_utils import run_with_retry
from accounts.models import Account, TOTPDevice
from accounts.permissions import IsSuperUserRole, IsTOTPEnrolled
from accounts.serializers import student_onboarding_status
from django_api.models import StudentProfile
from django_api.services import load_profile_data
from project_superuser import services
from project_superuser.serializers import (
    ADMIN_PATCHABLE_UNIVERSITY_FIELDS,
    KB_SYNCED_UNIVERSITY_FIELDS,
    AdminCreateStudentSerializer,
    AdminCreateSuperuserSerializer,
    AdminEnrollUniversitySerializer,
)
from universities.models import University

SUPERUSER_PERMISSIONS = [IsAuthenticated, IsTOTPEnrolled, IsSuperUserRole]


def _error(message: str, http_status=status.HTTP_400_BAD_REQUEST) -> Response:
    return Response({"status": "error", "message": str(message)}, status=http_status)


def _serialize_account(account: Account) -> Dict[str, Any]:
    user = account.user
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.first_name,
        "role": account.role,
        "student_id": account.student_id,
        "university_id": account.university_id,
        "is_active": user.is_active,
        "totp_enrolled": TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).exists(),
        "date_joined": user.date_joined,
    }


def _serialize_university(university: University) -> Dict[str, Any]:
    from universities.services import university_setup_status

    return {
        "id": university.id,
        "name": university.name,
        "agent_name": university.agent_name,
        "location": university.location,
        "tagline": university.tagline,
        "description": university.description,
        "contact_email": university.contact_email,
        "contact_phone": university.contact_phone,
        "website_url": university.website_url,
        "admissions_office_address": university.admissions_office_address,
        "eligibility_criteria": university.eligibility_criteria,
        "scrape_urls": university.scrape_urls,
        "tone_descriptors": university.tone_descriptors,
        "best_fit_notes": university.best_fit_notes,
        "not_best_fit_notes": university.not_best_fit_notes,
        "communication_style_notes": university.communication_style_notes,
        "never_do_notes": university.never_do_notes,
        "officer_count": Account.objects.filter(university_id=university.id, role=Account.Role.UNIVERSITY).count(),
        "setup_status": university_setup_status(university.id),
        "created_at": university.created_at,
        "updated_at": university.updated_at,
    }


# ---------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------

class AdminStudentListCreateAPIView(APIView):
    """
    GET /api/superuser/students/   ?search=<email substring>
    POST /api/superuser/students/  Body: {"email", "password", "name"}
    """

    permission_classes = SUPERUSER_PERMISSIONS

    def get(self, request):
        accounts = Account.objects.filter(role=Account.Role.STUDENT).select_related("user").order_by("-created_at")

        search = request.query_params.get("search", "").strip()
        if search:
            accounts = accounts.filter(user__email__icontains=search)

        profiles_by_id = {
            p.student_id: p
            for p in StudentProfile.objects.filter(student_id__in=[a.student_id for a in accounts])
        }

        students = []
        for account in accounts:
            profile = profiles_by_id.get(account.student_id)
            students.append({
                **_serialize_account(account),
                "institution": profile.institution if profile else "",
                "major": profile.major if profile else "",
                "verified": profile.verified if profile else False,
            })

        return Response({"students": students})

    def post(self, request):
        serializer = AdminCreateStudentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = run_with_retry(serializer.save)
        return Response(_serialize_account(user.account), status=status.HTTP_201_CREATED)


class AdminStudentDetailAPIView(APIView):
    """
    GET /api/superuser/students/<student_id>/
    DELETE /api/superuser/students/<student_id>/
        Removes the login (User, cascading Account/TOTP/GitHub OAuth) and
        purges the StudentProfile and everything else keyed by student_id
        (see project_superuser.services.purge_student_data).
    """

    permission_classes = SUPERUSER_PERMISSIONS

    def _get_account(self, student_id: str):
        return Account.objects.filter(role=Account.Role.STUDENT, student_id=student_id).select_related("user").first()

    def get(self, request, student_id: str):
        account = self._get_account(student_id)
        if account is None:
            return _error("Student not found.", status.HTTP_404_NOT_FOUND)

        data = _serialize_account(account)
        data["profile"] = load_profile_data(student_id)
        data["onboarding"] = student_onboarding_status(student_id)
        return Response(data)

    def delete(self, request, student_id: str):
        account = self._get_account(student_id)
        if account is None:
            return _error("Student not found.", status.HTTP_404_NOT_FOUND)

        def _do_delete():
            with transaction.atomic():
                account.user.delete()
                services.purge_student_data(student_id)

        run_with_retry(_do_delete)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------
# Universities
# ---------------------------------------------------------------------

class AdminUniversityListCreateAPIView(APIView):
    """
    GET /api/superuser/universities/   ?search=<name substring>
    POST /api/superuser/universities/  Body:
        {
          "institution_name": "...",
          "profile": {...any of ADMIN_PATCHABLE_UNIVERSITY_FIELDS...},
          "officer_email": "...", "officer_password": "...", "officer_name": "..."
        }
    officer_email/officer_password are optional -- omit them to enroll just
    the University record and create the officer login separately later.
    """

    permission_classes = SUPERUSER_PERMISSIONS

    def get(self, request):
        universities = University.objects.all()

        search = request.query_params.get("search", "").strip()
        if search:
            universities = universities.filter(name__icontains=search)

        return Response({"universities": [_serialize_university(u) for u in universities]})

    def post(self, request):
        serializer = AdminEnrollUniversitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        university = run_with_retry(serializer.save)
        return Response(_serialize_university(university), status=status.HTTP_201_CREATED)


class AdminUniversityDetailAPIView(APIView):
    """
    GET /api/superuser/universities/<university_id>/
    PATCH /api/superuser/universities/<university_id>/  Body: any of ADMIN_PATCHABLE_UNIVERSITY_FIELDS
    DELETE /api/superuser/universities/<university_id>/
        Refuses (409) while officer accounts still reference this
        university_id -- remove/reassign them via /api/superuser/users/
        first, so no login is left pointing at a deleted university.
    """

    permission_classes = SUPERUSER_PERMISSIONS

    def get(self, request, university_id: str):
        university = University.objects.filter(pk=university_id).first()
        if university is None:
            return _error("University not found.", status.HTTP_404_NOT_FOUND)
        return Response(_serialize_university(university))

    def patch(self, request, university_id: str):
        university = University.objects.filter(pk=university_id).first()
        if university is None:
            return _error("University not found.", status.HTTP_404_NOT_FOUND)

        data = request.data or {}
        changed_kb_fields = False
        touched = False
        for field in ADMIN_PATCHABLE_UNIVERSITY_FIELDS:
            if field not in data:
                continue
            setattr(university, field, data[field])
            touched = True
            if field in KB_SYNCED_UNIVERSITY_FIELDS:
                changed_kb_fields = True

        if not touched:
            return _error(f"Provide at least one of: {', '.join(sorted(ADMIN_PATCHABLE_UNIVERSITY_FIELDS))}.")

        university.save()

        if changed_kb_fields:
            from universities.services import sync_profile_facts_to_kb

            sync_profile_facts_to_kb(university)

        return Response(_serialize_university(university))

    def delete(self, request, university_id: str):
        university = University.objects.filter(pk=university_id).first()
        if university is None:
            return _error("University not found.", status.HTTP_404_NOT_FOUND)

        if Account.objects.filter(university_id=university_id, role=Account.Role.UNIVERSITY).exists():
            return _error(
                "This university still has officer accounts. Remove or reassign them via "
                "/api/superuser/users/ first.",
                status.HTTP_409_CONFLICT,
            )

        def _do_delete():
            with transaction.atomic():
                university.delete()
                services.purge_university_data(university_id)

        run_with_retry(_do_delete)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------
# Users (cross-role)
# ---------------------------------------------------------------------

class AdminUserListAPIView(APIView):
    """
    GET /api/superuser/users/   ?role=student|university|superuser   &search=<email substring>
    Every login account across all roles -- the cross-role view /students/
    and /universities/ don't give you.
    """

    permission_classes = SUPERUSER_PERMISSIONS

    def get(self, request):
        accounts = Account.objects.select_related("user").order_by("-created_at")

        role = request.query_params.get("role", "").strip()
        if role:
            accounts = accounts.filter(role=role)

        search = request.query_params.get("search", "").strip()
        if search:
            accounts = accounts.filter(user__email__icontains=search)

        return Response({"users": [_serialize_account(a) for a in accounts]})


class AdminCreateSuperuserAPIView(APIView):
    """POST /api/superuser/users/create-superuser/  Body: {"email", "password", "name"}"""

    permission_classes = SUPERUSER_PERMISSIONS

    def post(self, request):
        serializer = AdminCreateSuperuserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = run_with_retry(serializer.save)
        return Response(_serialize_account(user.account), status=status.HTTP_201_CREATED)


class AdminUserDetailAPIView(APIView):
    """
    GET /api/superuser/users/<user_id>/
    PATCH /api/superuser/users/<user_id>/  Body: {"is_active": true|false}
    DELETE /api/superuser/users/<user_id>/
        Removes only this login (User, cascading Account/TOTP/GitHub OAuth).
        Does not touch the underlying StudentProfile/University row -- use
        /api/superuser/students/<id>/ or /api/superuser/universities/<id>/
        for a full data purge.
    A superuser may not deactivate or delete their own account through this
    endpoint, to avoid locking every superuser out at once.
    """

    permission_classes = SUPERUSER_PERMISSIONS

    def _get_account(self, user_id: int):
        return Account.objects.filter(user_id=user_id).select_related("user").first()

    def get(self, request, user_id: int):
        account = self._get_account(user_id)
        if account is None:
            return _error("User not found.", status.HTTP_404_NOT_FOUND)
        return Response(_serialize_account(account))

    def patch(self, request, user_id: int):
        account = self._get_account(user_id)
        if account is None:
            return _error("User not found.", status.HTTP_404_NOT_FOUND)

        if "is_active" not in (request.data or {}):
            return _error("is_active is required.")

        if account.user_id == request.user.id:
            return _error("You cannot change your own active status.")

        account.user.is_active = bool(request.data["is_active"])
        account.user.save(update_fields=["is_active"])
        return Response(_serialize_account(account))

    def delete(self, request, user_id: int):
        account = self._get_account(user_id)
        if account is None:
            return _error("User not found.", status.HTTP_404_NOT_FOUND)

        if account.user_id == request.user.id:
            return _error("You cannot delete your own account.")

        run_with_retry(account.user.delete)
        return Response(status=status.HTTP_204_NO_CONTENT)
