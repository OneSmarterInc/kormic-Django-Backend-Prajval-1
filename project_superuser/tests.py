from __future__ import annotations

import pyotp
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Account
from django_api.tests import _reset_inprocess_agent_caches, make_student_client, make_university_client
from universities.models import University


def make_superuser_client(email="root@example.com", password="S3curePassw0rd!"):
    """Bootstraps a superuser via the management command (the only way to
    create the first one), then drives it through the same TOTP enroll +
    verify flow every other role uses."""
    call_command("create_superuser_account", email=email, password=password, name="Root Admin")

    client = APIClient()
    access = client.post("/api/auth/login/", {"email": email, "password": password}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    secret = client.post("/api/auth/totp/enroll/").data["secret"]
    code = pyotp.TOTP(secret).now()
    client.post("/api/auth/totp/verify-enrollment/", {"code": code}, format="json")

    client.credentials()
    mfa_token = client.post("/api/auth/login/", {"email": email, "password": password}, format="json").data[
        "mfa_token"
    ]
    code = pyotp.TOTP(secret).now()
    tokens = client.post("/api/auth/verify-totp/", {"mfa_token": mfa_token, "code": code}, format="json").data
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


class SuperuserAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.admin = make_superuser_client()

    def test_register_endpoint_rejects_superuser_role(self):
        client = APIClient()
        resp = client.post(
            "/api/auth/register/",
            {"email": "sneaky@example.com", "password": "S3curePassw0rd!", "role": "superuser"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_is_forbidden(self):
        student, _ = make_student_client(email="stu1@example.com")
        resp = student.get("/api/superuser/students/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_university_officer_is_forbidden(self):
        officer = make_university_client(email="officer1@wsu.edu", university_id="wright_state_cs")
        resp = officer.get("/api/superuser/universities/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_is_unauthorized(self):
        resp = APIClient().get("/api/superuser/users/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class SuperuserStudentAPITests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.admin = make_superuser_client()

    def test_admin_can_create_list_get_and_delete_student(self):
        resp = self.admin.post(
            "/api/superuser/students/",
            {"email": "newstudent@example.com", "password": "S3curePassw0rd!", "name": "New Student"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        student_id = resp.data["student_id"]

        resp = self.admin.get("/api/superuser/students/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any(s["student_id"] == student_id for s in resp.data["students"]))

        resp = self.admin.get(f"/api/superuser/students/{student_id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["profile"]["name"], "New Student")

        resp = self.admin.delete(f"/api/superuser/students/{student_id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Account.objects.filter(student_id=student_id).exists())

        resp = self.admin.get(f"/api/superuser/students/{student_id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_email_rejected(self):
        payload = {"email": "dup@example.com", "password": "S3curePassw0rd!", "name": "Dup"}
        self.admin.post("/api/superuser/students/", payload, format="json")
        resp = self.admin.post("/api/superuser/students/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class SuperuserUniversityAPITests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.admin = make_superuser_client()

    def test_admin_can_enroll_university_with_officer(self):
        resp = self.admin.post(
            "/api/superuser/universities/",
            {
                "institution_name": "Test University",
                "officer_email": "officer_new@example.com",
                "officer_password": "S3curePassw0rd!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        university_id = resp.data["id"]
        self.assertTrue(University.objects.filter(pk=university_id).exists())
        self.assertTrue(Account.objects.filter(university_id=university_id, role=Account.Role.UNIVERSITY).exists())

    def test_enroll_without_officer_creates_bare_university(self):
        resp = self.admin.post(
            "/api/superuser/universities/",
            {"institution_name": "Bare University"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["officer_count"], 0)

    def test_cannot_delete_university_with_active_officers(self):
        resp = self.admin.post(
            "/api/superuser/universities/",
            {
                "institution_name": "Guarded University",
                "officer_email": "guard_officer@example.com",
                "officer_password": "S3curePassw0rd!",
            },
            format="json",
        )
        university_id = resp.data["id"]

        resp = self.admin.delete(f"/api/superuser/universities/{university_id}/")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(University.objects.filter(pk=university_id).exists())

    def test_patch_updates_profile_fields(self):
        resp = self.admin.post(
            "/api/superuser/universities/",
            {"institution_name": "Patchable University"},
            format="json",
        )
        university_id = resp.data["id"]

        resp = self.admin.patch(
            f"/api/superuser/universities/{university_id}/",
            {"description": "A great school.", "contact_email": "info@patchable.edu"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["description"], "A great school.")
        self.assertTrue(resp.data["setup_status"]["has_description"])


class SuperuserUserManagementTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.admin = make_superuser_client()

    def test_admin_cannot_delete_own_account(self):
        me = self.admin.get("/api/auth/me/").data
        resp = self.admin.delete(f"/api/superuser/users/{me['id']}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_cannot_deactivate_own_account(self):
        me = self.admin.get("/api/auth/me/").data
        resp = self.admin.patch(f"/api/superuser/users/{me['id']}/", {"is_active": False}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_can_create_second_superuser(self):
        resp = self.admin.post(
            "/api/superuser/users/create-superuser/",
            {"email": "second_admin@example.com", "password": "S3curePassw0rd!", "name": "Second Admin"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["role"], "superuser")

    def test_admin_can_deactivate_and_delete_a_student_login(self):
        student, student_id = make_student_client(email="tobedeactivated@example.com")
        user_id = student.get("/api/auth/me/").data["id"]

        resp = self.admin.patch(f"/api/superuser/users/{user_id}/", {"is_active": False}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["is_active"])

        resp = self.admin.delete(f"/api/superuser/users/{user_id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Account.objects.filter(student_id=student_id).exists())

    def test_users_list_can_filter_by_role(self):
        make_student_client(email="filterme@example.com")
        resp = self.admin.get("/api/superuser/users/?role=student")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(all(u["role"] == "student" for u in resp.data["users"]))
