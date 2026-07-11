from unittest import mock

import pyotp
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Account, TOTPDevice
from django_api.services import make_student_id

STUDENT_EMAIL = "student1@example.com"
STUDENT_ID = make_student_id(STUDENT_EMAIL)


def register(client, **overrides):
    payload = {
        "email": STUDENT_EMAIL,
        "password": "S3curePassw0rd!",
        "role": "student",
        "name": "Student One",
    }
    payload.update(overrides)
    return client.post("/api/auth/register/", payload, format="json")


def register_university(client, **overrides):
    payload = {
        "email": "officer1@wsu.edu",
        "password": "S3curePassw0rd!",
        "role": "university",
        "university_id": "wright_state_cs",
        "name": "Officer One",
    }
    payload.update(overrides)
    return client.post("/api/auth/register/", payload, format="json")


def login(client, email=STUDENT_EMAIL, password="S3curePassw0rd!"):
    return client.post("/api/auth/login/", {"email": email, "password": password}, format="json")


def enroll_and_confirm(client, access_token):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
    enroll_resp = client.post("/api/auth/totp/enroll/")
    secret = enroll_resp.data["secret"]
    code = pyotp.TOTP(secret).now()
    verify_resp = client.post("/api/auth/totp/verify-enrollment/", {"code": code}, format="json")
    return enroll_resp, verify_resp, secret


class AuthFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        cache.clear()

    def test_register_student_success(self):
        resp = register(self.client)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Account.objects.get(student_id=STUDENT_ID).role, "student")

    def test_register_student_ignores_client_supplied_student_id(self):
        resp = register(self.client, student_id="someone-elses-id")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["user"]["student_id"], STUDENT_ID)
        self.assertFalse(Account.objects.filter(student_id="someone-elses-id").exists())

    def test_register_university_valid_id_success(self):
        resp = register_university(self.client)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Account.objects.get(university_id="wright_state_cs").role, "university")

    def test_register_university_unknown_id_rejected(self):
        resp = register_university(self.client, university_id="mit_cs", email="officer2@mit.edu")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_duplicate_email_rejected(self):
        register(self.client)
        resp = register(self.client)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_duplicate_student_id_rejected(self):
        # Different emails that normalize (via make_student_id) to the same slug.
        register(self.client, email="student.one@example.com")
        resp = register(self.client, email="student+one@example.com")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student_id", resp.data)

    def test_login_unenrolled_user_gets_restricted_token(self):
        register(self.client)
        resp = login(self.client)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["must_enroll_totp"])
        self.assertIn("access", resp.data)
        self.assertNotIn("refresh", resp.data)

    def test_restricted_token_blocked_from_protected_endpoint(self):
        register(self.client)
        access = login(self.client).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.get(f"/api/profile/{STUDENT_ID}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_restricted_token_allows_enroll_and_verify_enrollment(self):
        register(self.client)
        access = login(self.client).data["access"]
        enroll_resp, verify_resp, _ = enroll_and_confirm(self.client, access)
        self.assertEqual(enroll_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(verify_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(verify_resp.data["backup_codes"]), 10)
        device = TOTPDevice.objects.get(user__account__student_id=STUDENT_ID)
        self.assertIsNotNone(device.confirmed_at)

    def test_same_access_token_now_passes_totp_gate_after_enrollment(self):
        register(self.client)
        access = login(self.client).data["access"]
        enroll_and_confirm(self.client, access)
        # Reuse the SAME pre-enrollment token, no re-login.
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.get(f"/api/profile/{STUDENT_ID}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)  # profile gate passes, just no profile yet

    def test_login_enrolled_user_gets_mfa_token_not_direct_tokens(self):
        register(self.client)
        access = login(self.client).data["access"]
        enroll_and_confirm(self.client, access)
        self.client.credentials()
        resp = login(self.client)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["must_enroll_totp"])
        self.assertIn("mfa_token", resp.data)
        self.assertNotIn("access", resp.data)

    def test_verify_totp_success_issues_tokens(self):
        register(self.client)
        access = login(self.client).data["access"]
        _, _, secret = enroll_and_confirm(self.client, access)
        self.client.credentials()
        mfa_token = login(self.client).data["mfa_token"]
        code = pyotp.TOTP(secret).now()
        resp = self.client.post("/api/auth/verify-totp/", {"mfa_token": mfa_token, "code": code}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

    def test_verify_totp_wrong_code_rejected(self):
        register(self.client)
        access = login(self.client).data["access"]
        enroll_and_confirm(self.client, access)
        self.client.credentials()
        mfa_token = login(self.client).data["mfa_token"]
        resp = self.client.post("/api/auth/verify-totp/", {"mfa_token": mfa_token, "code": "000000"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_totp_throttled_after_max_attempts(self):
        register(self.client)
        access = login(self.client).data["access"]
        _, _, secret = enroll_and_confirm(self.client, access)
        self.client.credentials()
        mfa_token = login(self.client).data["mfa_token"]
        for _ in range(5):
            self.client.post("/api/auth/verify-totp/", {"mfa_token": mfa_token, "code": "000000"}, format="json")
        code = pyotp.TOTP(secret).now()
        resp = self.client.post("/api/auth/verify-totp/", {"mfa_token": mfa_token, "code": code}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_verify_totp_expired_mfa_token_rejected(self):
        register(self.client)
        access = login(self.client).data["access"]
        enroll_and_confirm(self.client, access)
        self.client.credentials()
        resp = self.client.post(
            "/api/auth/verify-totp/", {"mfa_token": "not-a-real-token", "code": "123456"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_verify_totp_backup_code_accepted_once(self):
        register(self.client)
        access = login(self.client).data["access"]
        _, verify_resp, _ = enroll_and_confirm(self.client, access)
        backup_code = verify_resp.data["backup_codes"][0]

        self.client.credentials()
        mfa_token = login(self.client).data["mfa_token"]
        resp1 = self.client.post(
            "/api/auth/verify-totp/", {"mfa_token": mfa_token, "code": backup_code}, format="json"
        )
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)

        mfa_token_2 = login(self.client).data["mfa_token"]
        resp2 = self.client.post(
            "/api/auth/verify-totp/", {"mfa_token": mfa_token_2, "code": backup_code}, format="json"
        )
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_wrong_password_rejected(self):
        register(self.client)
        resp = login(self.client, password="wrong-password")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_and_logout_flow(self):
        register(self.client)
        access = login(self.client).data["access"]
        _, _, secret = enroll_and_confirm(self.client, access)
        self.client.credentials()
        mfa_token = login(self.client).data["mfa_token"]
        code = pyotp.TOTP(secret).now()
        tokens = self.client.post(
            "/api/auth/verify-totp/", {"mfa_token": mfa_token, "code": code}, format="json"
        ).data

        refresh_resp = self.client.post("/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(refresh_resp.status_code, status.HTTP_200_OK)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        logout_resp = self.client.post("/api/auth/logout/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(logout_resp.status_code, status.HTTP_205_RESET_CONTENT)

        refresh_after_logout = self.client.post("/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(refresh_after_logout.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_allowed_while_restricted(self):
        register(self.client)
        access = login(self.client).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.post("/api/auth/logout/", {"refresh": "irrelevant"}, format="json")
        # Not blocked by the TOTP gate -- rejected only because "irrelevant" isn't a real refresh token.
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_me_endpoint_reports_role_and_totp_status(self):
        register(self.client)
        access = login(self.client).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.get("/api/auth/me/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["role"], "student")
        self.assertFalse(resp.data["totp_enrolled"])

        enroll_and_confirm(self.client, access)
        resp2 = self.client.get("/api/auth/me/")
        self.assertTrue(resp2.data["totp_enrolled"])

    def test_me_endpoint_reports_onboarding_status(self):
        register(self.client)
        access = login(self.client).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        enroll_and_confirm(self.client, access)

        resp = self.client.get("/api/auth/me/")
        onboarding = resp.data["onboarding"]
        self.assertFalse(onboarding["profile_exists"])
        self.assertFalse(onboarding["resume_uploaded"])
        self.assertFalse(onboarding["github_connected"])
        self.assertFalse(onboarding["linkedin_connected"])
        self.assertFalse(onboarding["setup_complete"])

        self.client.post(
            "/api/profile/",
            {"github": "https://github.com/octocat", "linkedin_url": "https://linkedin.com/in/octocat"},
            format="json",
        )

        resp2 = self.client.get("/api/auth/me/")
        onboarding2 = resp2.data["onboarding"]
        self.assertTrue(onboarding2["profile_exists"])
        self.assertTrue(onboarding2["github_connected"])
        self.assertTrue(onboarding2["linkedin_connected"])
        self.assertFalse(onboarding2["resume_uploaded"])
        self.assertFalse(onboarding2["setup_complete"])  # resume still missing

    @mock.patch("agents.linkedin_agent.LinkedInAgent")
    def test_onboarding_reports_linkedin_connected_after_image_upload(self, MockLinkedInAgent):
        # LinkedIn is captured via image upload + parsing, not a typed URL --
        # linkedin_connected must not depend on profile.linkedin_url alone.
        MockLinkedInAgent.return_value.extract.return_value = {"skills": []}

        register(self.client)
        access = login(self.client).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        enroll_and_confirm(self.client, access)

        image = SimpleUploadedFile("screenshot.png", b"fake-image-bytes", content_type="image/png")
        upload_resp = self.client.post("/api/profile/linkedin/", {"images": image}, format="multipart")
        self.assertEqual(upload_resp.status_code, status.HTTP_200_OK)

        resp = self.client.get("/api/auth/me/")
        self.assertTrue(resp.data["onboarding"]["linkedin_connected"])
