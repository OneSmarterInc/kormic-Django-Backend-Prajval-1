from unittest import mock

import pyotp
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from agents import commons as agents_commons
from django_api.models import FitAssessment, ResumeUpload, StudentProfile
from django_api.services import make_student_id


def _reset_inprocess_agent_caches():
    # agents.commons keeps plain module-level dict caches (_university_agents /
    # _profile_presenters) that persist across TestCase classes within the
    # same test process -- clear them so a mocked agent from one test doesn't
    # leak into another via the get_*_agent() cache lookup. There is no
    # per-student context cache to clear on the pure_multi_agent chat path --
    # it loads student_profile/memory/agent_name fresh from the database on
    # every turn.
    agents_commons._university_agents.clear()
    agents_commons._profile_presenters.clear()


def _register_and_enroll(client, *, role, email, password="S3curePassw0rd!", **extra):
    payload = {"email": email, "password": password, "role": role, "name": "Test User"}
    payload.update(extra)
    client.post("/api/auth/register/", payload, format="json")

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
    return tokens


def make_student_client(email="student_a@example.com"):
    client = APIClient()
    _register_and_enroll(client, role="student", email=email)
    return client, make_student_id(email)


def make_university_client(email="officer_a@wsu.edu", university_id="wright_state_cs"):
    client = APIClient()
    _register_and_enroll(client, role="university", email=email, university_id=university_id)
    return client


class OwnershipTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student_a, self.student_a_id = make_student_client(email="a@example.com")
        self.student_b, self.student_b_id = make_student_client(email="b@example.com")
        self.officer_wsu = make_university_client(email="officer1@wsu.edu", university_id="wright_state_cs")
        self.officer_franklin = make_university_client(email="officer1@franklin.edu", university_id="franklin_cs")

    def test_student_can_create_and_read_own_profile(self):
        resp = self.student_a.post("/api/profile/", {"name": "Alice"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["student_id"], self.student_a_id)

        get_resp = self.student_a.get(f"/api/profile/{self.student_a_id}/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)

    def test_student_cannot_read_other_students_profile(self):
        self.student_a.post("/api/profile/", {"name": "Alice"}, format="json")
        resp = self.student_b.get(f"/api/profile/{self.student_a_id}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_client_supplied_student_id_is_ignored(self):
        resp = self.student_a.post(
            "/api/profile/", {"student_id": self.student_b_id, "name": "Alice"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["student_id"], self.student_a_id)
        self.assertFalse(StudentProfile.objects.filter(student_id=self.student_b_id).exists())

    def test_blank_numeric_fields_do_not_400(self):
        resp = self.student_a.post(
            "/api/profile/",
            {
                "name": "Alice",
                "graduation_year": "",
                "gpa": "",
                "gre_quant": "",
                "gre_verbal": "",
                "toefl": "",
                "ielts": "",
                "budget": "",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_university_officer_can_read_own_dashboard(self):
        resp = self.officer_wsu.get("/api/university/wright_state_cs/profiles/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_university_officer_cannot_read_other_universitys_dashboard(self):
        resp = self.officer_wsu.get("/api/university/franklin_cs/profiles/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_token_rejected_on_university_only_endpoint(self):
        resp = self.student_a.get("/api/university/wright_state_cs/profiles/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_university_token_rejected_on_student_only_endpoint(self):
        self.student_a.post("/api/profile/", {"name": "Alice"}, format="json")
        resp = self.officer_wsu.get(f"/api/profile/{self.student_a_id}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_requests_now_rejected(self):
        anon = APIClient()
        resp = anon.get(f"/api/profile/{self.student_a_id}/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class ChatHistoryTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="c@example.com")
        self.student.post("/api/profile/", {"name": "Carol"}, format="json")

    @mock.patch("pure_multi_agent.runtime.run_turn")
    def test_agent_chat_persists_and_returns_history(self, mock_run_turn):
        mock_run_turn.side_effect = [
            ("Nova", "Hi there!"),
            ("Nova", "Sure, here's more info."),
        ]

        self.student.post("/api/chat/agent/", {"message": "Hello"}, format="json")
        self.student.post("/api/chat/agent/", {"message": "Tell me more"}, format="json")

        history = self.student.get("/api/chat/agent/history/")
        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertEqual(history.data["count"], 4)
        senders = [m["sender"] for m in history.data["messages"]]
        self.assertEqual(senders, ["user", "assistant", "user", "assistant"])


class SubResourceHistoryTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="d@example.com")
        self.student.post("/api/profile/", {"name": "Dave"}, format="json")
        self.officer_wsu = make_university_client(email="officer2@wsu.edu", university_id="wright_state_cs")
        self.officer_franklin = make_university_client(email="officer2@franklin.edu", university_id="franklin_cs")

    @mock.patch("agents.resume_parser.ResumeParserAgent")
    def test_resume_upload_history_accumulates_without_overwriting_files(self, MockParser):
        MockParser.return_value.parse.return_value = {"skills": ["Python"]}

        file1 = SimpleUploadedFile("resume.pdf", b"first-version", content_type="application/pdf")
        file2 = SimpleUploadedFile("resume.pdf", b"second-version", content_type="application/pdf")

        self.student.post("/api/profile/resume/", {"file": file1}, format="multipart")
        self.student.post("/api/profile/resume/", {"file": file2}, format="multipart")

        rows = ResumeUpload.objects.filter(student__student_id=self.student_id)
        self.assertEqual(rows.count(), 2)
        file_paths = {r.file_path for r in rows}
        self.assertEqual(len(file_paths), 2)  # distinct on-disk paths, no clobbering

        resp = self.student.get(f"/api/profile/{self.student_id}/resumes/")
        self.assertEqual(resp.data["count"], 2)

    @mock.patch("agents.resume_parser.ResumeParserAgent")
    def test_resume_download_scoped_to_owner_and_delete_removes_it(self, MockParser):
        MockParser.return_value.parse.return_value = {"skills": ["Python"]}
        file1 = SimpleUploadedFile("resume.pdf", b"resume-bytes", content_type="application/pdf")
        self.student.post("/api/profile/resume/", {"file": file1}, format="multipart")
        resume_id = ResumeUpload.objects.get(student__student_id=self.student_id).id

        download_resp = self.student.get(f"/api/profile/resume/{resume_id}/")
        self.assertEqual(download_resp.status_code, status.HTTP_200_OK)

        other_student, _ = make_student_client(email="e@example.com")
        forbidden_resp = other_student.get(f"/api/profile/resume/{resume_id}/")
        self.assertEqual(forbidden_resp.status_code, status.HTTP_403_FORBIDDEN)

        delete_resp = self.student.delete(f"/api/profile/resume/{resume_id}/")
        self.assertEqual(delete_resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ResumeUpload.objects.filter(id=resume_id).exists())

    def test_resume_detail_404_for_missing_resume(self):
        resp = self.student.get("/api/profile/resume/999999/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @mock.patch("agents.linkedin_agent.LinkedInAgent")
    def test_linkedin_image_history_download_and_ownership(self, MockLinkedInAgent):
        MockLinkedInAgent.return_value.extract.return_value = {"skills": ["Leadership"]}

        image = SimpleUploadedFile("screenshot.png", b"fake-image-bytes", content_type="image/png")
        upload_resp = self.student.post("/api/profile/linkedin/", {"images": image}, format="multipart")
        self.assertEqual(upload_resp.status_code, status.HTTP_200_OK)
        analysis_id = upload_resp.data["analysis_id"]
        self.assertTrue(
            upload_resp.data["images"][0]["uploaded_image_url"].endswith(
                f"/api/profile/linkedin/{analysis_id}/images/0/"
            )
        )

        history_resp = self.student.get(f"/api/profile/{self.student_id}/linkedin-history/")
        self.assertEqual(history_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(history_resp.data["analyses"][0]["id"], analysis_id)
        self.assertEqual(len(history_resp.data["analyses"][0]["images"]), 1)

        download_resp = self.student.get(f"/api/profile/linkedin/{analysis_id}/images/0/")
        self.assertEqual(download_resp.status_code, status.HTTP_200_OK)

        out_of_range_resp = self.student.get(f"/api/profile/linkedin/{analysis_id}/images/5/")
        self.assertEqual(out_of_range_resp.status_code, status.HTTP_404_NOT_FOUND)

        other_student, _ = make_student_client(email="f@example.com")
        forbidden_resp = other_student.get(f"/api/profile/linkedin/{analysis_id}/images/0/")
        self.assertEqual(forbidden_resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_profile_image_upload_replace_download_delete_and_officer_visibility(self):
        image1 = SimpleUploadedFile("avatar.png", b"first-image-bytes", content_type="image/png")
        upload_resp = self.student.post("/api/profile/image/", {"image": image1}, format="multipart")
        self.assertEqual(upload_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(
            upload_resp.data["profile_image_url"].endswith(f"/api/profile/{self.student_id}/image/")
        )

        first_path = StudentProfile.objects.get(student_id=self.student_id).profile_image_path
        self.assertNotEqual(first_path, "")

        download_resp = self.student.get(f"/api/profile/{self.student_id}/image/")
        self.assertEqual(download_resp.status_code, status.HTTP_200_OK)

        # Officers can view any student's picture (dashboard roster use case).
        officer_resp = self.officer_wsu.get(f"/api/profile/{self.student_id}/image/")
        self.assertEqual(officer_resp.status_code, status.HTTP_200_OK)

        # Another student is forbidden.
        other_student, _ = make_student_client(email="g@example.com")
        forbidden_resp = other_student.get(f"/api/profile/{self.student_id}/image/")
        self.assertEqual(forbidden_resp.status_code, status.HTTP_403_FORBIDDEN)

        # Re-uploading replaces rather than accumulating, and removes the old file.
        image2 = SimpleUploadedFile("avatar2.png", b"second-image-bytes", content_type="image/png")
        self.student.post("/api/profile/image/", {"image": image2}, format="multipart")
        second_path = StudentProfile.objects.get(student_id=self.student_id).profile_image_path
        self.assertNotEqual(first_path, second_path)
        from pathlib import Path
        self.assertFalse(Path(first_path).exists())

        # Officers cannot delete a student's picture -- owner only.
        officer_delete_resp = self.officer_wsu.delete(f"/api/profile/{self.student_id}/image/")
        self.assertEqual(officer_delete_resp.status_code, status.HTTP_403_FORBIDDEN)

        delete_resp = self.student.delete(f"/api/profile/{self.student_id}/image/")
        self.assertEqual(delete_resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(StudentProfile.objects.get(student_id=self.student_id).profile_image_path, "")

        missing_resp = self.student.get(f"/api/profile/{self.student_id}/image/")
        self.assertEqual(missing_resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_profile_image_upload_rejects_non_image_file(self):
        not_an_image = SimpleUploadedFile("resume.pdf", b"pdf-bytes", content_type="application/pdf")
        resp = self.student.post("/api/profile/image/", {"image": not_an_image}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_fit_assessment_history_dual_mode_visibility(self):
        # Fit assessments are only ever produced by the student's personal
        # agent via agents.commons.generate_fit_assessment (chat-triggered,
        # no direct student-facing POST endpoint anymore) -- create the rows
        # directly here to test the read-only history/detail views.
        student = StudentProfile.objects.get(student_id=self.student_id)
        FitAssessment.objects.create(
            student=student, university_id="wright_state_cs", assessment={"match_tier": "target", "match_score": 70}
        )
        FitAssessment.objects.create(
            student=student, university_id="franklin_cs", assessment={"match_tier": "target", "match_score": 70}
        )

        student_view = self.student.get(f"/api/assessments/{self.student_id}/")
        self.assertEqual(student_view.data["count"], 2)

        wsu_view = self.officer_wsu.get(f"/api/assessments/{self.student_id}/")
        self.assertEqual(wsu_view.data["count"], 1)
        self.assertEqual(wsu_view.data["assessments"][0]["university_id"], "wright_state_cs")

        franklin_view = self.officer_franklin.get(f"/api/assessments/{self.student_id}/")
        self.assertEqual(franklin_view.data["count"], 1)
        self.assertEqual(franklin_view.data["assessments"][0]["university_id"], "franklin_cs")

        self.assertEqual(FitAssessment.objects.filter(student__student_id=self.student_id).count(), 2)
