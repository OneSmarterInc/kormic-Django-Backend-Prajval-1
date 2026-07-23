from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from rest_framework import status

from django.utils import timezone
from datetime import timedelta

from django_api.models import ChatMessage, PendingQuery, StudentProfile
from django_api.tests import _reset_inprocess_agent_caches, make_student_client, make_university_client
from notifications.models import NotificationLog, PushToken


# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------

class PushTokenModelTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="pt1@example.com")

    def _account(self):
        from accounts.models import Account

        return Account.objects.get(student_id=self.student_id)

    def test_token_unique_across_accounts(self):
        PushToken.objects.create(account=self._account(), token="ExponentPushToken[abc]")
        with self.assertRaises(Exception):
            PushToken.objects.create(account=self._account(), token="ExponentPushToken[abc]")


# ---------------------------------------------------------------------
# Register / unregister endpoints
# ---------------------------------------------------------------------

class PushTokenEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="pt2@example.com")

    def test_register_requires_token(self):
        resp = self.student.post("/api/notifications/register-token/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_creates_token_for_own_account(self):
        resp = self.student.post(
            "/api/notifications/register-token/",
            {"token": "ExponentPushToken[xyz]", "platform": "ios"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        token = PushToken.objects.get(token="ExponentPushToken[xyz]")
        self.assertEqual(token.account.student_id, self.student_id)
        self.assertEqual(token.platform, "ios")
        self.assertTrue(token.is_active)

    def test_unknown_platform_falls_back_to_unknown(self):
        resp = self.student.post(
            "/api/notifications/register-token/",
            {"token": "ExponentPushToken[xyz2]", "platform": "windows-phone"},
            format="json",
        )
        self.assertEqual(resp.data["platform"], "unknown")

    def test_reregistering_existing_token_reassigns_owner(self):
        # Same physical device, first one student, then another logs in on it.
        other, other_id = make_student_client(email="pt3@example.com")

        self.student.post(
            "/api/notifications/register-token/", {"token": "ExponentPushToken[shared]"}, format="json"
        )
        other.post("/api/notifications/register-token/", {"token": "ExponentPushToken[shared]"}, format="json")

        token = PushToken.objects.get(token="ExponentPushToken[shared]")
        self.assertEqual(token.account.student_id, other_id)
        self.assertEqual(PushToken.objects.filter(token="ExponentPushToken[shared]").count(), 1)

    def test_unregister_deactivates_own_token(self):
        self.student.post(
            "/api/notifications/register-token/", {"token": "ExponentPushToken[gone]"}, format="json"
        )
        resp = self.student.post(
            "/api/notifications/unregister-token/", {"token": "ExponentPushToken[gone]"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["deactivated"])
        self.assertFalse(PushToken.objects.get(token="ExponentPushToken[gone]").is_active)

    def test_unregister_cannot_deactivate_someone_elses_token(self):
        other, _ = make_student_client(email="pt4@example.com")
        other.post("/api/notifications/register-token/", {"token": "ExponentPushToken[notyours]"}, format="json")

        resp = self.student.post(
            "/api/notifications/unregister-token/", {"token": "ExponentPushToken[notyours]"}, format="json"
        )
        self.assertFalse(resp.data["deactivated"])
        self.assertTrue(PushToken.objects.get(token="ExponentPushToken[notyours]").is_active)

    def test_unauthenticated_request_rejected(self):
        from rest_framework.test import APIClient

        resp = APIClient().post(
            "/api/notifications/register-token/", {"token": "ExponentPushToken[anon]"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------
# Service layer (notifications/services.py) -- Celery delivery is mocked out
# here; task logic itself is covered separately below.
# ---------------------------------------------------------------------

class NotificationServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="svc1@example.com")

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_notify_agent_reply_queues_push_without_touching_chat(self, mock_delay):
        from notifications.services import notify_agent_reply

        before = ChatMessage.objects.count()
        log = notify_agent_reply(student_id=self.student_id, agent_name="Nova", reply="Here is your roadmap.")

        self.assertIsNotNone(log)
        self.assertEqual(log.event_type, NotificationLog.EventType.AGENT_REPLY)
        self.assertEqual(log.title, "Nova replied")
        self.assertEqual(log.body, "Here is your roadmap.")
        mock_delay.assert_called_once_with(log.id)
        self.assertEqual(ChatMessage.objects.count(), before)  # log_chat_turn already wrote it, not our job

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_notify_agent_reply_truncates_long_body(self, mock_delay):
        from notifications.services import notify_agent_reply

        long_reply = "x" * 500
        log = notify_agent_reply(student_id=self.student_id, agent_name="Nova", reply=long_reply)
        self.assertEqual(len(log.body), 120)
        self.assertTrue(log.body.endswith("..."))

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_notify_agent_reply_unknown_student_returns_none(self, mock_delay):
        from notifications.services import notify_agent_reply

        result = notify_agent_reply(student_id="no-such-student", agent_name="Nova", reply="hi")
        self.assertIsNone(result)
        mock_delay.assert_not_called()

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_send_agent_message_writes_chat_message_and_queues_push(self, mock_delay):
        from notifications.services import send_agent_message

        before = ChatMessage.objects.filter(student_id=self.student_id, channel=ChatMessage.Channel.AGENT).count()
        log = send_agent_message(student_id=self.student_id, content="Checking in!", title="Hey there")

        self.assertIsNotNone(log)
        after = ChatMessage.objects.filter(student_id=self.student_id, channel=ChatMessage.Channel.AGENT).count()
        self.assertEqual(after, before + 1)
        msg = ChatMessage.objects.filter(student_id=self.student_id, channel=ChatMessage.Channel.AGENT).latest(
            "created_at"
        )
        self.assertEqual(msg.sender, ChatMessage.Sender.ASSISTANT)
        self.assertEqual(msg.content, "Checking in!")
        mock_delay.assert_called_once_with(log.id)

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_notify_pending_query_resolved_includes_answer_in_chat_and_push(self, mock_delay):
        from notifications.services import notify_pending_query_resolved

        log = notify_pending_query_resolved(
            student_id=self.student_id,
            university_id="wright_state_cs",
            question="What's the deadline?",
            answer="March 1st.",
            query_id=42,
        )

        self.assertEqual(log.event_type, NotificationLog.EventType.PENDING_QUERY_RESOLVED)
        self.assertIn("March 1st.", log.body)
        self.assertEqual(log.data["query_id"], 42)

        msg = ChatMessage.objects.filter(
            student_id=self.student_id, channel=ChatMessage.Channel.AGENT
        ).latest("created_at")
        self.assertIn("March 1st.", msg.content)
        self.assertEqual(msg.meta["query_id"], 42)
        mock_delay.assert_called_once_with(log.id)


# ---------------------------------------------------------------------
# Celery task logic (notifications/tasks.py) -- Expo HTTP calls are mocked;
# tasks are invoked directly (bypassing the broker) so no Redis is needed.
# ---------------------------------------------------------------------

class SendPushNotificationTaskTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="task1@example.com")
        from accounts.models import Account

        self.account = Account.objects.get(student_id=self.student_id)
        self.log = NotificationLog.objects.create(
            account=self.account,
            event_type=NotificationLog.EventType.AGENT_REPLY,
            title="Nova replied",
            body="Hi!",
        )

    def test_skips_when_no_active_token(self):
        from notifications.tasks import send_push_notification_task

        send_push_notification_task(self.log.id)

        self.log.refresh_from_db()
        self.assertEqual(self.log.status, NotificationLog.Status.SKIPPED_NO_TOKEN)

    def test_missing_log_is_a_noop(self):
        from notifications.tasks import send_push_notification_task

        # Must not raise even though no such NotificationLog exists.
        send_push_notification_task(999999)

    @mock.patch("notifications.tasks.check_push_receipts_task.apply_async")
    @mock.patch("notifications.expo.send_expo_push_messages")
    def test_successful_send_marks_sent_and_schedules_receipt_check(self, mock_send, mock_apply_async):
        from notifications.tasks import send_push_notification_task

        token = PushToken.objects.create(account=self.account, token="ExponentPushToken[ok]")
        mock_send.return_value = [{"status": "ok", "id": "receipt-1"}]

        send_push_notification_task(self.log.id)

        self.log.refresh_from_db()
        self.assertEqual(self.log.status, NotificationLog.Status.SENT)
        mock_send.assert_called_once()
        sent_messages = mock_send.call_args[0][0]
        self.assertEqual(sent_messages[0]["to"], token.token)
        self.assertEqual(sent_messages[0]["title"], "Nova replied")
        mock_apply_async.assert_called_once_with(args=[{"receipt-1": token.token}], countdown=20)

    @mock.patch("notifications.tasks.check_push_receipts_task.apply_async")
    @mock.patch("notifications.expo.send_expo_push_messages")
    def test_device_not_registered_ticket_deactivates_token(self, mock_send, mock_apply_async):
        from notifications.tasks import send_push_notification_task

        token = PushToken.objects.create(account=self.account, token="ExponentPushToken[dead]")
        mock_send.return_value = [
            {"status": "error", "message": "not registered", "details": {"error": "DeviceNotRegistered"}}
        ]

        send_push_notification_task(self.log.id)

        token.refresh_from_db()
        self.assertFalse(token.is_active)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, NotificationLog.Status.SENT)
        mock_apply_async.assert_not_called()  # no successful ticket, nothing to check receipts for

    @mock.patch("notifications.expo.send_expo_push_messages")
    def test_expo_failure_marks_failed_and_retries(self, mock_send):
        from notifications.tasks import send_push_notification_task

        PushToken.objects.create(account=self.account, token="ExponentPushToken[flaky]")
        mock_send.side_effect = RuntimeError("Expo is down")

        with mock.patch.object(send_push_notification_task, "retry", side_effect=RuntimeError("retried")) as mock_retry:
            with self.assertRaises(RuntimeError):
                send_push_notification_task(self.log.id)
            mock_retry.assert_called_once()

        self.log.refresh_from_db()
        self.assertEqual(self.log.status, NotificationLog.Status.FAILED)
        self.assertIn("Expo is down", self.log.error)


class CheckPushReceiptsTaskTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="task2@example.com")
        from accounts.models import Account

        self.account = Account.objects.get(student_id=self.student_id)
        self.token = PushToken.objects.create(account=self.account, token="ExponentPushToken[receipt]")

    @mock.patch("notifications.expo.get_expo_push_receipts")
    def test_device_not_registered_receipt_deactivates_token(self, mock_receipts):
        from notifications.tasks import check_push_receipts_task

        mock_receipts.return_value = {
            "receipt-1": {"status": "error", "message": "gone", "details": {"error": "DeviceNotRegistered"}}
        }

        check_push_receipts_task({"receipt-1": self.token.token})

        self.token.refresh_from_db()
        self.assertFalse(self.token.is_active)

    @mock.patch("notifications.expo.get_expo_push_receipts")
    def test_ok_receipt_leaves_token_active(self, mock_receipts):
        from notifications.tasks import check_push_receipts_task

        mock_receipts.return_value = {"receipt-1": {"status": "ok"}}
        check_push_receipts_task({"receipt-1": self.token.token})

        self.token.refresh_from_db()
        self.assertTrue(self.token.is_active)

    @mock.patch("notifications.expo.get_expo_push_receipts")
    def test_receipt_lookup_failure_retries(self, mock_receipts):
        from notifications.tasks import check_push_receipts_task

        mock_receipts.side_effect = RuntimeError("network blip")
        with mock.patch.object(check_push_receipts_task, "retry", side_effect=RuntimeError("retried")) as mock_retry:
            with self.assertRaises(RuntimeError):
                check_push_receipts_task({"receipt-1": self.token.token})
            mock_retry.assert_called_once()


# ---------------------------------------------------------------------
# End-to-end wiring: the three student-facing trigger points.
# ---------------------------------------------------------------------

class AgentChatNotificationTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="e2e1@example.com")

    @mock.patch("notifications.services.send_push_notification_task.delay")
    @mock.patch("pure_multi_agent.runtime.run_turn")
    def test_agent_chat_queues_notification_after_reply(self, mock_run_turn, mock_delay):
        mock_run_turn.return_value = ("Nova", "Sure, here's the info you asked for.")

        resp = self.student.post("/api/chat/agent/", {"message": "Tell me about MIT"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        log = NotificationLog.objects.get(account__student_id=self.student_id)
        self.assertEqual(log.event_type, NotificationLog.EventType.AGENT_REPLY)
        self.assertIn("Sure, here's the info", log.body)
        mock_delay.assert_called_once_with(log.id)

    @mock.patch("notifications.services.send_push_notification_task.delay")
    @mock.patch("pure_multi_agent.runtime.run_turn")
    def test_agent_chat_still_succeeds_if_notification_enqueue_fails(self, mock_run_turn, mock_delay):
        # A broken notification path (e.g. Celery/Redis down) must never break
        # the actual chat response the student is waiting on.
        mock_run_turn.return_value = ("Nova", "Reply text")
        mock_delay.side_effect = RuntimeError("broker unreachable")

        resp = self.student.post("/api/chat/agent/", {"message": "hi"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["reply"], "Reply text")


class PendingQueryResolutionNotificationTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="e2e2@example.com")
        self.officer = make_university_client(email="officer-e2e@wsu.edu", university_id="wright_state_cs")
        self.query = PendingQuery.objects.create(
            university_id="wright_state_cs",
            student_id=self.student_id,
            student_name="Test Student",
            question="What is the application deadline?",
            status=PendingQuery.Status.PENDING,
        )

    @mock.patch("notifications.services.send_push_notification_task.delay")
    @mock.patch("agents.commons.get_university_agent")
    def test_resolving_query_notifies_student_and_appends_chat_message(self, mock_get_agent, mock_delay):
        mock_agent = mock.Mock()
        mock_agent.resolve_pending_query.return_value = True
        mock_get_agent.return_value = mock_agent

        resp = self.officer.post(
            "/api/queries/answer/",
            {"query_id": self.query.id, "answer": "The deadline is March 1st.", "answered_by": "Jane"},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        log = NotificationLog.objects.get(account__student_id=self.student_id)
        self.assertEqual(log.event_type, NotificationLog.EventType.PENDING_QUERY_RESOLVED)
        self.assertIn("March 1st", log.body)
        mock_delay.assert_called_once_with(log.id)

        chat_msg = ChatMessage.objects.filter(
            student_id=self.student_id, channel=ChatMessage.Channel.AGENT
        ).latest("created_at")
        self.assertIn("March 1st", chat_msg.content)

    @mock.patch("notifications.services.send_push_notification_task.delay")
    @mock.patch("agents.commons.get_university_agent")
    def test_resolving_query_without_student_id_skips_notification(self, mock_get_agent, mock_delay):
        mock_agent = mock.Mock()
        mock_agent.resolve_pending_query.return_value = True
        mock_get_agent.return_value = mock_agent

        orphan_query = PendingQuery.objects.create(
            university_id="wright_state_cs",
            student_id="",
            question="General question with no student attached.",
            status=PendingQuery.Status.PENDING,
        )

        resp = self.officer.post(
            "/api/queries/answer/",
            {"query_id": orphan_query.id, "answer": "General answer.", "answered_by": "Jane"},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_delay.assert_not_called()


class AgentInitiatedMessageCommandTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="e2e3@example.com")

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_command_sends_proactive_message(self, mock_delay):
        call_command("send_agent_message", self.student_id, "We found a great match for you!", "--title=New match")

        log = NotificationLog.objects.get(account__student_id=self.student_id)
        self.assertEqual(log.title, "New match")
        self.assertEqual(log.event_type, NotificationLog.EventType.AGENT_INITIATED)
        mock_delay.assert_called_once_with(log.id)

        chat_msg = ChatMessage.objects.filter(
            student_id=self.student_id, channel=ChatMessage.Channel.AGENT
        ).latest("created_at")
        self.assertEqual(chat_msg.content, "We found a great match for you!")

    def test_command_errors_for_unknown_student(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("send_agent_message", "no-such-student", "hi")


# ---------------------------------------------------------------------
# Proactive agent outreach (notifications/proactive.py) -- the agent
# noticing something and messaging the student without being asked first.
# ---------------------------------------------------------------------

class BuildCheckinMessageTests(TestCase):
    """Pure-function tests: no DB, compute_profile_intelligence is mocked so
    these don't depend on the exact scoring thresholds in django_api.services."""

    def _mock_intelligence(self, weaknesses=None, missing_items=None):
        return mock.patch(
            "django_api.services.compute_profile_intelligence",
            return_value={
                "weaknesses": weaknesses or [],
                "profile_completeness": {"missing_items": missing_items or []},
            },
        )

    def test_real_gaps_take_priority_over_weaknesses_and_missing_items(self):
        from notifications.proactive import build_checkin_message

        profile = {"gaps": ["Research interests", "GitHub profile", "budget", "target_disciplines"]}
        with self._mock_intelligence(weaknesses=["some weakness"], missing_items=["some field"]):
            message = build_checkin_message(profile)

        self.assertIn("Research interests", message)
        self.assertIn("GitHub profile", message)
        self.assertNotIn("some weakness", message)
        self.assertNotIn("budget", message)  # placeholder gap filtered out

    def test_falls_back_to_weaknesses_when_only_placeholder_gaps(self):
        from notifications.proactive import build_checkin_message

        profile = {"gaps": ["budget", "target_disciplines"]}
        with self._mock_intelligence(weaknesses=["GPA is not provided."], missing_items=["LinkedIn profile"]):
            message = build_checkin_message(profile)

        self.assertIn("GPA is not provided.", message)

    def test_falls_back_to_missing_items_when_no_gaps_or_weaknesses(self):
        from notifications.proactive import build_checkin_message

        profile = {"gaps": []}
        with self._mock_intelligence(weaknesses=[], missing_items=["LinkedIn profile"]):
            message = build_checkin_message(profile)

        self.assertIn("LinkedIn profile", message)

    def test_returns_none_when_nothing_to_say(self):
        from notifications.proactive import build_checkin_message

        profile = {"gaps": ["budget", "target_disciplines"]}
        with self._mock_intelligence(weaknesses=[], missing_items=[]):
            message = build_checkin_message(profile)

        self.assertIsNone(message)

    def test_single_talking_point_phrasing(self):
        from notifications.proactive import build_checkin_message

        profile = {"gaps": ["Missing GitHub profile."]}
        with self._mock_intelligence():
            message = build_checkin_message(profile)

        self.assertTrue(message.startswith("Quick suggestion -- Missing GitHub profile."))

    def test_multi_talking_point_phrasing(self):
        from notifications.proactive import build_checkin_message

        profile = {"gaps": ["Gap one", "Gap two"]}
        with self._mock_intelligence():
            message = build_checkin_message(profile)

        self.assertIn("Gap one", message)
        self.assertIn("Gap two", message)
        self.assertIn("a few gaps", message)


class RunCheckinForStudentTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student, self.student_id = make_student_client(email="proactive1@example.com")
        StudentProfile.objects.update_or_create(
            student_id=self.student_id, defaults={"name": "Carol", "gaps": ["GitHub profile is missing."]}
        )
        from accounts.models import Account

        self.account = Account.objects.get(student_id=self.student_id)

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_sends_and_writes_chat_message_when_gap_exists(self, mock_delay):
        from notifications.proactive import run_checkin_for_student

        log = run_checkin_for_student(self.student_id)

        self.assertIsNotNone(log)
        self.assertEqual(log.event_type, NotificationLog.EventType.PROACTIVE_CHECKIN)
        self.assertIn("GitHub profile is missing.", log.body)
        mock_delay.assert_called_once_with(log.id)

        chat_msg = ChatMessage.objects.filter(
            student_id=self.student_id, channel=ChatMessage.Channel.AGENT
        ).latest("created_at")
        self.assertIn("GitHub profile is missing.", chat_msg.content)

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_second_call_within_cooldown_is_skipped(self, mock_delay):
        from notifications.proactive import run_checkin_for_student

        first = run_checkin_for_student(self.student_id, cooldown_days=7)
        second = run_checkin_for_student(self.student_id, cooldown_days=7)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        mock_delay.assert_called_once()

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_sends_again_after_cooldown_expires(self, mock_delay):
        from notifications.proactive import run_checkin_for_student

        stale_log = NotificationLog.objects.create(
            account=self.account,
            event_type=NotificationLog.EventType.PROACTIVE_CHECKIN,
            title="Quick suggestion from your agent",
            body="old nudge",
        )
        stale_log.created_at = timezone.now() - timedelta(days=8)
        stale_log.save(update_fields=["created_at"])

        result = run_checkin_for_student(self.student_id, cooldown_days=7)

        self.assertIsNotNone(result)
        mock_delay.assert_called_once_with(result.id)

    def test_unknown_student_returns_none(self):
        from notifications.proactive import run_checkin_for_student

        self.assertIsNone(run_checkin_for_student("no-such-student"))

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_complete_profile_with_no_gaps_sends_nothing(self, mock_delay):
        from notifications.proactive import run_checkin_for_student

        StudentProfile.objects.update_or_create(student_id=self.student_id, defaults={"gaps": []})
        with mock.patch("django_api.services.compute_profile_intelligence") as mock_intel:
            mock_intel.return_value = {
                "weaknesses": [],
                "profile_completeness": {"missing_items": []},
            }
            result = run_checkin_for_student(self.student_id)

        self.assertIsNone(result)
        mock_delay.assert_not_called()


class ProactiveCheckinTaskTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_inprocess_agent_caches()
        self.student_a, self.student_a_id = make_student_client(email="proactive-a@example.com")
        self.student_b, self.student_b_id = make_student_client(email="proactive-b@example.com")
        StudentProfile.objects.update_or_create(
            student_id=self.student_a_id, defaults={"name": "Alice", "gaps": ["No GitHub profile."]}
        )
        StudentProfile.objects.update_or_create(
            student_id=self.student_b_id, defaults={"name": "Bob", "gaps": ["No GitHub profile."]}
        )

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_task_nudges_every_eligible_student_once(self, mock_delay):
        from notifications.tasks import run_proactive_checkins_task

        result = run_proactive_checkins_task()

        self.assertEqual(result["sent"], 2)
        self.assertEqual(
            NotificationLog.objects.filter(event_type=NotificationLog.EventType.PROACTIVE_CHECKIN).count(), 2
        )

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_task_skips_students_without_a_real_account(self, mock_delay):
        from notifications.tasks import run_proactive_checkins_task

        StudentProfile.objects.create(student_id="ghost-profile", name="Ghost", gaps=["orphan gap"])

        result = run_proactive_checkins_task()

        self.assertEqual(result["sent"], 2)  # only Alice and Bob, not the ghost profile
        self.assertEqual(result["skipped"], 0)

    @mock.patch("notifications.services.send_push_notification_task.delay")
    def test_task_second_run_same_day_skips_everyone(self, mock_delay):
        from notifications.tasks import run_proactive_checkins_task

        run_proactive_checkins_task()
        second_result = run_proactive_checkins_task()

        self.assertEqual(second_result["sent"], 0)
        self.assertEqual(second_result["skipped"], 2)
