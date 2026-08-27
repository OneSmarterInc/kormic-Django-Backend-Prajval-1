from __future__ import annotations

import json
from unittest import mock

from django.test import TestCase

from django_api.models import AgentConversationLog
from django_api.tests import _reset_inprocess_agent_caches
from pure_multi_agent.tools.university_tools import build_tools
from universities.models import University


def _fake_response(payload: dict):
    class FakeBlock:
        def __init__(self, text):
            self.text = text

    class FakeResponse:
        def __init__(self, text):
            self.content = [FakeBlock(text)]

    return FakeResponse(json.dumps(payload))


class AskUniversityToolConversationLoggingTests(TestCase):
    """
    A3: ask_university is the actual student-agent -> university-agent
    boundary the student's personal agent calls through -- the
    AgentConversationLog row must get written here, not just be reachable
    from the lower-level UniversityAgent.answer() it wraps.
    """

    def setUp(self):
        _reset_inprocess_agent_caches()
        u = University.objects.create(name="Tool Log University", agent_name="Nova5")
        self.university_id = str(u.uuid)
        self.ctx = {
            "canonical_student_id": "student_tool_log",
            "student_profile": {"student_id": "student_tool_log", "name": "Tester"},
        }
        self.tools = {t.name: t for t in build_tools(self.ctx)}

    @mock.patch("agents.university_agent._get_anthropic_client")
    def test_ask_university_logs_conversation(self, mock_client):
        mock_client.return_value.messages.create.return_value = _fake_response({
            "answer": "The deadline is March 1.",
            "confidence": 0.9,
            "unsupported_topics": [],
        })

        result = self.tools["ask_university"].invoke({
            "university_id": self.university_id,
            "question": "What is the deadline?",
        })

        self.assertIn("March 1", result)
        log = AgentConversationLog.objects.get()
        self.assertEqual(log.asker.owner_id, "student_tool_log")
        self.assertEqual(log.responder.owner_id, self.university_id)
        self.assertEqual(log.question, "What is the deadline?")
        self.assertIn("March 1", log.answer)
        self.assertEqual(log.confidence, 0.9)

    @mock.patch("agents.university_agent._get_anthropic_client")
    def test_unknown_university_id_does_not_log(self, mock_client):
        result = self.tools["ask_university"].invoke({
            "university_id": "no_such_university",
            "question": "What is the deadline?",
        })

        self.assertIn("Unknown university_id", result)
        self.assertEqual(AgentConversationLog.objects.count(), 0)
