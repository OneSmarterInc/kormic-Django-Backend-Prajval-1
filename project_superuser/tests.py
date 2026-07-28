from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pyotp
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Account, TOTPBackupCode, TOTPDevice
from django_api.tests import _reset_inprocess_agent_caches, make_student_client, make_university_client
from project_superuser.models import ActivityLog
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

    def test_admin_can_enroll_university_with_admin_account(self):
        resp = self.admin.post(
            "/api/superuser/universities/",
            {
                "institution_name": "Test University",
                "email": "admin_new@example.com",
                "password": "S3curePassw0rd!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        university_id = resp.data["id"]
        self.assertTrue(University.objects.filter(pk=university_id).exists())
        self.assertTrue(Account.objects.filter(university_id=university_id, role=Account.Role.UNIVERSITY).exists())
        self.assertEqual(resp.data["officer_count"], 1)

    def test_enroll_university_requires_admin_credentials(self):
        resp = self.admin.post(
            "/api/superuser/universities/",
            {"institution_name": "Bare University"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(University.objects.filter(name="Bare University").exists())

    def test_new_university_admin_must_enroll_own_totp(self):
        # The superuser only creates the login -- TOTP enrollment happens
        # self-serve, on the university's own first login.
        resp = self.admin.post(
            "/api/superuser/universities/",
            {
                "institution_name": "Self Enroll University",
                "email": "self_enroll_admin@example.com",
                "password": "S3curePassw0rd!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        login_resp = APIClient().post(
            "/api/auth/login/",
            {"email": "self_enroll_admin@example.com", "password": "S3curePassw0rd!"},
            format="json",
        )
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(login_resp.data["must_enroll_totp"])
        self.assertEqual(login_resp.data["user"]["role"], "university")

    def test_cannot_delete_university_with_active_officers(self):
        resp = self.admin.post(
            "/api/superuser/universities/",
            {
                "institution_name": "Guarded University",
                "email": "guard_admin@example.com",
                "password": "S3curePassw0rd!",
            },
            format="json",
        )
        university_id = resp.data["id"]

        resp = self.admin.delete(f"/api/superuser/universities/{university_id}/")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(University.objects.filter(pk=university_id).exists())

    def test_university_detail_includes_admin_and_university_email(self):
        resp = self.admin.post(
            "/api/superuser/universities/",
            {
                "institution_name": "Emailed University",
                "email": "admin_emailed@example.com",
                "password": "S3curePassw0rd!",
                "name": "Jane Admin",
                "profile": {"contact_email": "info@emailed.edu"},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        university_id = resp.data["id"]
        self.assertEqual(resp.data["admin_email"], "admin_emailed@example.com")
        self.assertEqual(resp.data["admin_name"], "Jane Admin")
        self.assertEqual(resp.data["university_email"], "info@emailed.edu")

        resp = self.admin.get(f"/api/superuser/universities/{university_id}/")
        self.assertEqual(resp.data["admin_email"], "admin_emailed@example.com")
        self.assertEqual(resp.data["university_email"], "info@emailed.edu")

    def test_patch_updates_profile_fields(self):
        resp = self.admin.post(
            "/api/superuser/universities/",
            {
                "institution_name": "Patchable University",
                "email": "patchable_admin@example.com",
                "password": "S3curePassw0rd!",
            },
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


class SuperuserUserRemoveTOTPTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.admin = make_superuser_client()

    def test_admin_can_remove_a_students_totp(self):
        student, _ = make_student_client(email="losttotp@example.com")
        user_id = student.get("/api/auth/me/").data["id"]
        self.assertTrue(TOTPDevice.objects.filter(user_id=user_id, confirmed_at__isnull=False).exists())

        resp = self.admin.post(f"/api/superuser/users/{user_id}/remove-totp/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["totp_enrolled"])
        self.assertFalse(TOTPDevice.objects.filter(user_id=user_id).exists())
        self.assertFalse(TOTPBackupCode.objects.filter(user_id=user_id).exists())

        self.assertTrue(
            ActivityLog.objects.filter(action=ActivityLog.Action.TOTP_REMOVED, target_email="losttotp@example.com").exists()
        )

        login_resp = APIClient().post(
            "/api/auth/login/", {"email": "losttotp@example.com", "password": "S3curePassw0rd!"}, format="json"
        )
        self.assertTrue(login_resp.data["must_enroll_totp"])

    def test_admin_cannot_remove_own_totp(self):
        me = self.admin.get("/api/auth/me/").data
        resp = self.admin.post(f"/api/superuser/users/{me['id']}/remove-totp/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(TOTPDevice.objects.filter(user_id=me["id"]).exists())

    def test_remove_totp_for_unknown_user_is_404(self):
        resp = self.admin.post("/api/superuser/users/999999/remove-totp/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class SuperuserUserResetPasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.admin = make_superuser_client()

    def test_admin_can_reset_a_students_password_and_it_revokes_sessions(self):
        student, _ = make_student_client(email="forgotpw@example.com")
        user_id = student.get("/api/auth/me/").data["id"]

        resp = self.admin.post(
            f"/api/superuser/users/{user_id}/reset-password/",
            {"password": "BrandNewPassw0rd!"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        old_login = APIClient().post(
            "/api/auth/login/", {"email": "forgotpw@example.com", "password": "S3curePassw0rd!"}, format="json"
        )
        self.assertEqual(old_login.status_code, status.HTTP_401_UNAUTHORIZED)

        new_login = APIClient().post(
            "/api/auth/login/", {"email": "forgotpw@example.com", "password": "BrandNewPassw0rd!"}, format="json"
        )
        self.assertEqual(new_login.status_code, status.HTTP_200_OK)

        self.assertTrue(
            ActivityLog.objects.filter(action=ActivityLog.Action.PASSWORD_RESET, target_email="forgotpw@example.com").exists()
        )

    def test_reset_password_requires_password_field(self):
        student, _ = make_student_client(email="nopassword@example.com")
        user_id = student.get("/api/auth/me/").data["id"]
        resp = self.admin.post(f"/api/superuser/users/{user_id}/reset-password/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reset_password_rejects_weak_password(self):
        student, _ = make_student_client(email="weakpw@example.com")
        user_id = student.get("/api/auth/me/").data["id"]
        resp = self.admin.post(
            f"/api/superuser/users/{user_id}/reset-password/", {"password": "123"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class SuperuserUserRevokeSessionsTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.admin = make_superuser_client()

    def test_admin_can_revoke_a_students_sessions(self):
        student, _ = make_student_client(email="revokeme@example.com")
        user_id = student.get("/api/auth/me/").data["id"]

        resp = self.admin.post(f"/api/superuser/users/{user_id}/revoke-sessions/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data["revoked_sessions"], 1)

        self.assertTrue(
            ActivityLog.objects.filter(action=ActivityLog.Action.SESSIONS_REVOKED, target_email="revokeme@example.com").exists()
        )

        # A second call has nothing new left to blacklist.
        resp = self.admin.post(f"/api/superuser/users/{user_id}/revoke-sessions/")
        self.assertEqual(resp.data["revoked_sessions"], 0)


class SuperuserAuditLogTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.admin = make_superuser_client()

    def test_audit_log_lists_actions_and_filters_by_action(self):
        student, _ = make_student_client(email="audited@example.com")
        user_id = student.get("/api/auth/me/").data["id"]
        self.admin.post(f"/api/superuser/users/{user_id}/remove-totp/")

        resp = self.admin.get("/api/superuser/audit-log/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any(e["target_email"] == "audited@example.com" for e in resp.data["entries"]))

        resp = self.admin.get("/api/superuser/audit-log/?action=password_reset")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(any(e["target_email"] == "audited@example.com" for e in resp.data["entries"]))


class AuthActivityLoggingTests(TestCase):
    """The audit log isn't just admin-initiated actions -- register/login/
    logout on the public /api/auth/ endpoints get logged onto the same
    ActivityLog model (see accounts.views), so a superadmin can see full
    auth activity for any student or university account, not just what
    other superusers have done to it."""

    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.admin = make_superuser_client()

    def test_register_is_logged(self):
        APIClient().post(
            "/api/auth/register/",
            {"email": "newreg@example.com", "password": "S3curePassw0rd!", "role": "student"},
            format="json",
        )
        entry = ActivityLog.objects.get(action=ActivityLog.Action.REGISTERED, target_email="newreg@example.com")
        self.assertEqual(entry.target_role, "student")
        self.assertEqual(entry.actor_email, "newreg@example.com")
        self.assertTrue(entry.target_student_id)
        self.assertEqual(entry.target_university_id, "")

    def test_university_officer_login_is_tagged_with_its_university_id(self):
        make_university_client(email="officer_login@wsu.edu", university_id="tagged_univ")
        entry = ActivityLog.objects.filter(
            action=ActivityLog.Action.LOGIN_SUCCEEDED, target_email="officer_login@wsu.edu"
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.target_university_id, "tagged_univ")
        self.assertEqual(entry.target_role, "university")
        self.assertEqual(entry.target_student_id, "")

        # The audit log's university_id filter finds it, and finds nothing
        # for an unrelated university.
        resp = self.admin.get("/api/superuser/audit-log/?university_id=tagged_univ")
        self.assertTrue(any(e["target_email"] == "officer_login@wsu.edu" for e in resp.data["entries"]))
        self.assertTrue(all(e["target_university_id"] == "tagged_univ" for e in resp.data["entries"]))

        resp = self.admin.get("/api/superuser/audit-log/?university_id=some_other_univ")
        self.assertFalse(any(e["target_email"] == "officer_login@wsu.edu" for e in resp.data["entries"]))

    def test_login_failure_with_wrong_password_is_logged_against_the_real_account(self):
        make_student_client(email="wrongpw@example.com")
        APIClient().post(
            "/api/auth/login/", {"email": "wrongpw@example.com", "password": "not-the-password"}, format="json"
        )
        entry = ActivityLog.objects.filter(
            action=ActivityLog.Action.LOGIN_FAILED, target_email="wrongpw@example.com"
        ).first()
        self.assertIsNotNone(entry)
        self.assertIsNotNone(entry.target_user_id)
        self.assertEqual(entry.target_role, "student")

    def test_login_failure_for_unknown_email_is_logged_without_a_target_user(self):
        APIClient().post(
            "/api/auth/login/", {"email": "doesnotexist@example.com", "password": "whatever123"}, format="json"
        )
        entry = ActivityLog.objects.get(
            action=ActivityLog.Action.LOGIN_FAILED, target_email="doesnotexist@example.com"
        )
        self.assertIsNone(entry.target_user_id)
        self.assertEqual(entry.target_role, "")

    def test_full_login_flow_logs_exactly_two_successes_no_premature_ones(self):
        # make_student_client drives the fixture through two *complete*
        # logins: one right after register (TOTP not enrolled yet, so
        # that /login/ call issues real tokens directly -- a genuine
        # success), and one full password+MFA login afterward. Each should
        # log exactly one LOGIN_SUCCEEDED; there must be no extra/premature
        # entry logged when the second login's step 1 merely issues an
        # mfa_token (MFA still pending at that point, not yet a success).
        student, _ = make_student_client(email="fulllogin@example.com")
        successes = ActivityLog.objects.filter(
            action=ActivityLog.Action.LOGIN_SUCCEEDED, target_email="fulllogin@example.com"
        )
        self.assertEqual(successes.count(), 2)

    def test_wrong_totp_code_at_verify_step_logs_login_failed(self):
        student, _ = make_student_client(email="badtotp@example.com")
        user_id = student.get("/api/auth/me/").data["id"]

        login_resp = APIClient().post(
            "/api/auth/login/", {"email": "badtotp@example.com", "password": "S3curePassw0rd!"}, format="json"
        )
        mfa_token = login_resp.data["mfa_token"]
        resp = APIClient().post(
            "/api/auth/verify-totp/", {"mfa_token": mfa_token, "code": "000000"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        entry = ActivityLog.objects.filter(
            action=ActivityLog.Action.LOGIN_FAILED, target_user_id=user_id
        ).first()
        self.assertIsNotNone(entry)

    def test_logout_is_logged(self):
        student, _ = make_student_client(email="logmeout@example.com")
        me = student.get("/api/auth/me/").data
        # make_student_client doesn't keep the refresh token around, so
        # mint a fresh session the same way and log it out explicitly.
        login_resp = APIClient().post(
            "/api/auth/login/", {"email": "logmeout@example.com", "password": "S3curePassw0rd!"}, format="json"
        )
        mfa_token = login_resp.data["mfa_token"]
        secret = TOTPDevice.objects.get(user_id=me["id"]).secret
        # +30s (next time-step): make_student_client's own login already
        # consumed *this instant's* code via the replay-cache guard in
        # accounts/totp.py, so reusing pyotp.TOTP(secret).now() here would
        # be rejected as a replay rather than exercising a real second login.
        code = pyotp.TOTP(secret).at(datetime.now(dt_timezone.utc) + timedelta(seconds=30))
        tokens = APIClient().post(
            "/api/auth/verify-totp/", {"mfa_token": mfa_token, "code": code}, format="json"
        ).data

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        resp = client.post("/api/auth/logout/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_205_RESET_CONTENT)

        self.assertTrue(
            ActivityLog.objects.filter(action=ActivityLog.Action.LOGGED_OUT, target_email="logmeout@example.com").exists()
        )

    def test_audit_log_can_filter_by_role(self):
        make_student_client(email="rolefilter_student@example.com")
        make_university_client(email="rolefilter_officer@wsu.edu", university_id="role_filter_univ")

        resp = self.admin.get("/api/superuser/audit-log/?role=university")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any(e["target_email"] == "rolefilter_officer@wsu.edu" for e in resp.data["entries"]))
        self.assertFalse(any(e["target_email"] == "rolefilter_student@example.com" for e in resp.data["entries"]))
