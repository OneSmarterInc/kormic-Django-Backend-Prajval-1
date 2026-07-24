import json
from unittest import mock

from django.test import TestCase

from agents.university_agent import UniversityAgent
from django_api.models import PendingQuery
from universities.models import University


def _fake_response(payload: dict):
    class FakeBlock:
        def __init__(self, text):
            self.text = text

    class FakeResponse:
        def __init__(self, text):
            self.content = [FakeBlock(text)]

    return FakeResponse(json.dumps(payload))


class UniversityAgentPartialEscalationTests(TestCase):
    """
    A compound question ("what are the deadlines and funding") can be
    strongly supported on one topic (deadlines) and have zero knowledge
    base coverage on another (funding). The single overall confidence score
    the model reports reflects the topics it COULD answer, so it can clear
    MIN_CONFIDENCE even though part of the question is unanswered -- that
    gap must still reach the university as a real PendingQuery instead of
    being silently absorbed into a high confidence score and left for the
    student to "email the university" on their own.
    """

    def setUp(self):
        University.objects.create(id="write_state", name="Write State", agent_name="Nova2")
        self.agent = UniversityAgent("write_state", auto_scrape=False)
        self.agent.kb.store(
            topic="Application deadlines",
            content="Fall 2025 deadline is March 1; Spring 2026 is October 10.",
            source_type="seed",
            confidence=1.0,
        )

    @mock.patch("agents.university_agent._get_anthropic_client")
    def test_compound_question_escalates_unsupported_topic_despite_high_overall_confidence(self, mock_client):
        mock_client.return_value.messages.create.return_value = _fake_response({
            "answer": (
                "Deadlines: Fall 2025 is March 1, Spring 2026 is October 10. "
                "Funding isn't documented for Write State."
            ),
            "confidence": 0.85,
            "unsupported_topics": ["funding"],
        })

        result = self.agent.answer("What are the deadlines and funding?")

        self.assertFalse(result["pending"])
        self.assertTrue(result.get("partial_pending"))
        self.assertEqual(result["unsupported_topics"], ["funding"])

        query = PendingQuery.objects.get(id=result["pending_query"]["query_id"])
        self.assertEqual(query.university_id, "write_state")
        self.assertIn("funding", query.question.lower())

    @mock.patch("agents.university_agent._get_anthropic_client")
    def test_fully_supported_answer_does_not_escalate(self, mock_client):
        mock_client.return_value.messages.create.return_value = _fake_response({
            "answer": "Fall 2025 deadline is March 1.",
            "confidence": 0.9,
            "unsupported_topics": [],
        })

        result = self.agent.answer("What is the fall deadline?")

        self.assertFalse(result["pending"])
        self.assertNotIn("partial_pending", result)
        self.assertEqual(PendingQuery.objects.count(), 0)
