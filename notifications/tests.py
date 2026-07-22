from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from rest_framework import status

from django_api.models import ChatMessage, PendingQuery
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
