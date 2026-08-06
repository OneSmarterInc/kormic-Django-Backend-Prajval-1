"""
Claim-flow tests. The adversarial ones mirror the spec's security core:
possession of a token reveals nothing beyond a masked email; wrong codes are
counted and limited; a claimed invitation is dead; edits are recorded as
divergences; re-uploads never overwrite claimed rows.
"""
import io
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Account
from django_api.models import StudentProfile
from django_api.services import make_student_id
from institutes.services import register_institute

from .models import ListedStudent, UniversityStudentList

CSV = (
    "full_name,email,field_of_study,degree_level,expected_graduation,phone\n"
    "Priya Sharma,priya.sharma@gmail.com,Computer Engineering,Masters,05/2027,+91 90000 00001\n"
    "Arjun Rao,arjun.rao@gmail.com,Data Science,Masters,05/2027,\n"
    "Bad Row,not-an-email,CS,Masters,05/2027,\n"
)


def _code_from_outbox() -> str:
    body = mail.outbox[-1].body
    return re.search(r"(\d{6})", body).group(1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ClaimFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.institute = register_institute("Wright State Feeder Institute", contact_email="ops@wsfi.edu")
        user = get_user_model().objects.create_user(
            username="officer@wsfi.edu", email="officer@wsfi.edu", password="x"
        )
        Account.objects.create(user=user, role=Account.Role.INSTITUTE, institute_id=self.institute.id)
        self.client.force_authenticate(user=user)
        resp = self.client.post(
            "/api/institute-lists/upload/",
            {
                "file": io.BytesIO(CSV.encode()),
                "institute_id": self.institute.id,
                "contact_name": "Dr. John",
                "contact_email": "john@wsfi.edu",
                "contact_verification": "institutional email domain",
            },
            format="multipart",
        )
        self.upload = resp.json()
        self.client.force_authenticate(user=None)  # claim side is anonymous

    # ------------------------------------------------------------------ intake

    def test_upload_ingests_with_provenance_and_rejects_bad_rows(self):
        self.assertEqual(self.upload["accepted"], 2)
        self.assertEqual(len(self.upload["rejected"]), 1)
        lst = UniversityStudentList.objects.get(id=self.upload["list_id"])
        self.assertEqual(lst.contact_name, "Dr. John")
        self.assertEqual(lst.row_count, 2)
        self.assertEqual(
            ListedStudent.objects.filter(status=ListedStudent.Status.UNCLAIMED).count(), 2
        )

    # ------------------------------------------------------------- claim: start

    def test_start_reveals_only_masked_email(self):
        row = ListedStudent.objects.get(email="priya.sharma@gmail.com")
        resp = self.client.post("/api/claim/start/", {"token": row.claim_token}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"masked_email": "p•••••@gmail.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("gmail.com", mail.outbox[0].to[0])

    def test_start_is_generic_for_unknown_email(self):
        resp = self.client.post("/api/claim/start/", {"email": "stranger@gmail.com"}, format="json")
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("stranger", str(resp.json()))

    # ------------------------------------------------------------ claim: verify

    def test_wrong_code_counted_and_limited(self):
        self.client.post("/api/claim/start/", {"email": "priya.sharma@gmail.com"}, format="json")
        for _ in range(5):
            resp = self.client.post(
                "/api/claim/verify/",
                {"email": "priya.sharma@gmail.com", "code": "000000"},
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(
            "/api/claim/verify/", {"email": "priya.sharma@gmail.com", "code": "000000"}, format="json"
        )
        self.assertEqual(resp.status_code, 429)

    def test_correct_code_returns_prefill(self):
        self.client.post("/api/claim/start/", {"email": "priya.sharma@gmail.com"}, format="json")
        resp = self.client.post(
            "/api/claim/verify/",
            {"email": "priya.sharma@gmail.com", "code": _code_from_outbox()},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["prefill"]["full_name"], "Priya Sharma")
        self.assertEqual(data["prefill"]["institute_name"], "Wright State Feeder Institute")
        self.assertIn("claim_session", data)

    # ----------------------------------------------------------- claim: confirm

    def _verified_session(self, email="priya.sharma@gmail.com"):
        self.client.post("/api/claim/start/", {"email": email}, format="json")
        resp = self.client.post(
            "/api/claim/verify/", {"email": email, "code": _code_from_outbox()}, format="json"
        )
        return resp.json()["claim_session"]

    def test_confirm_records_divergence_and_creates_sourced_profile(self):
        session = self._verified_session()
        resp = self.client.post(
            "/api/claim/confirm/",
            {
                "claim_session": session,
                "fields": {"field_of_study": "Computer Science", "email": "attacker@evil.com"},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["divergences_recorded"], 1)
        self.assertTrue(data["badge"]["institute_sourced"])

        row = ListedStudent.objects.get(email="priya.sharma@gmail.com")
        self.assertEqual(row.status, ListedStudent.Status.CLAIMED)
        self.assertEqual(row.divergences[0]["field"], "field_of_study")

        profile = StudentProfile.objects.get(student_id=make_student_id("priya.sharma@gmail.com"))
        # the claim address was NOT editable -- attacker@evil.com ignored
        self.assertEqual(profile.email, "priya.sharma@gmail.com")
        self.assertEqual(profile.major, "Computer Science")
        self.assertEqual(profile.extra_data["institute_sourced"]["institute_id"], self.institute.id)

    def test_claimed_invitation_is_dead(self):
        session = self._verified_session()
        self.client.post("/api/claim/confirm/", {"claim_session": session, "fields": {}}, format="json")
        # token now dead for start...
        row = ListedStudent.objects.get(email="priya.sharma@gmail.com")
        resp = self.client.post("/api/claim/start/", {"token": row.claim_token}, format="json")
        self.assertEqual(resp.status_code, 404)
        # ...and the session cannot confirm twice
        resp = self.client.post("/api/claim/confirm/", {"claim_session": session, "fields": {}}, format="json")
        self.assertEqual(resp.status_code, 400)

    # -------------------------------------------------------------- reconciliation

    def test_reupload_never_overwrites_claimed(self):
        session = self._verified_session()
        self.client.post("/api/claim/confirm/", {"claim_session": session, "fields": {}}, format="json")

        user = get_user_model().objects.get(username="officer@wsfi.edu")
        self.client.force_authenticate(user=user)
        changed = CSV.replace("Priya Sharma", "Priya CHANGED")
        resp = self.client.post(
            "/api/institute-lists/upload/",
            {
                "file": io.BytesIO(changed.encode()),
                "institute_id": self.institute.id,
                "contact_name": "Dr. John",
                "contact_email": "john@wsfi.edu",
            },
            format="multipart",
        )
        data = resp.json()
        self.assertEqual(len(data["skipped_claimed"]), 1)
        profile = StudentProfile.objects.get(student_id=make_student_id("priya.sharma@gmail.com"))
        self.assertEqual(profile.name, "Priya Sharma")  # untouched

    # -------------------------------------------------------------- ownership

    def test_institute_cannot_upload_for_a_different_institute(self):
        other = register_institute("Some Other Institute")
        user = get_user_model().objects.get(username="officer@wsfi.edu")
        self.client.force_authenticate(user=user)
        resp = self.client.post(
            "/api/institute-lists/upload/",
            {
                "file": io.BytesIO(CSV.encode()),
                "institute_id": other.id,
                "contact_name": "Dr. John",
                "contact_email": "john@wsfi.edu",
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 403)

    # -------------------------------------------------------------- roster + invites

    def test_list_students_shows_roster_for_own_list(self):
        user = get_user_model().objects.get(username="officer@wsfi.edu")
        self.client.force_authenticate(user=user)
        resp = self.client.get(f"/api/institute-lists/lists/{self.upload['list_id']}/students/")
        self.assertEqual(resp.status_code, 200)
        emails = {s["email"] for s in resp.json()["students"]}
        self.assertEqual(emails, {"priya.sharma@gmail.com", "arjun.rao@gmail.com"})

    def test_list_students_rejects_a_different_institute(self):
        other = register_institute("Some Other Institute")
        other_user = get_user_model().objects.create_user(
            username="officer@other.edu", email="officer@other.edu", password="x"
        )
        Account.objects.create(user=other_user, role=Account.Role.INSTITUTE, institute_id=other.id)
        self.client.force_authenticate(user=other_user)
        resp = self.client.get(f"/api/institute-lists/lists/{self.upload['list_id']}/students/")
        self.assertEqual(resp.status_code, 403)

    def test_list_lists_shows_claimed_and_unclaimed_counts(self):
        session = self._verified_session()
        self.client.post("/api/claim/confirm/", {"claim_session": session, "fields": {}}, format="json")

        user = get_user_model().objects.get(username="officer@wsfi.edu")
        self.client.force_authenticate(user=user)
        resp = self.client.get("/api/institute-lists/lists/")
        self.assertEqual(resp.status_code, 200)
        lst = resp.json()["lists"][0]
        self.assertEqual(lst["claimed_count"], 1)
        self.assertEqual(lst["unclaimed_count"], 1)

    @override_settings(CLAIM_PAGE_URL="https://app.kormic.example/claim")
    def test_send_invites_emails_unclaimed_rows_and_is_idempotent(self):
        user = get_user_model().objects.get(username="officer@wsfi.edu")
        self.client.force_authenticate(user=user)
        mail.outbox = []

        resp = self.client.post(f"/api/institute-lists/lists/{self.upload['list_id']}/send-invites/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["invites_sent"], 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("https://app.kormic.example/claim?token=", mail.outbox[0].body)

        # calling again does not re-invite already-invited unclaimed rows
        resp = self.client.post(f"/api/institute-lists/lists/{self.upload['list_id']}/send-invites/")
        self.assertEqual(resp.json()["invites_sent"], 0)

    def test_send_invites_requires_claim_page_url_configured(self):
        user = get_user_model().objects.get(username="officer@wsfi.edu")
        self.client.force_authenticate(user=user)
        resp = self.client.post(f"/api/institute-lists/lists/{self.upload['list_id']}/send-invites/")
        self.assertEqual(resp.status_code, 500)
