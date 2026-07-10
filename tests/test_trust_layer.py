from ..knowledge.trust_layer import (
    is_high_risk_question,
    calculate_confidence,
    build_trust_summary,
)


class DummyEntry:
    def __init__(self, topic, content, source_type, confidence=1.0, source_url=None):
        self.topic = topic
        self.content = content
        self.source_type = source_type
        self.confidence = confidence
        self.source_url = source_url
        self.times_used = 0


def test_high_risk_question_detected():
    assert is_high_risk_question("What is the application deadline?")
    assert is_high_risk_question("Is funding available?")
    assert is_high_risk_question("What GRE score is required?")
    assert is_high_risk_question("What is the tuition fee?")


def test_normal_question_not_high_risk():
    assert not is_high_risk_question("What makes Wright State different?")


def test_confidence_high_for_scraped_evidence():
    entries = [
        DummyEntry(
            topic="Program Overview",
            content="Wright State offers MS CS.",
            source_type="scraped",
            confidence=1.0,
        )
    ]

    result = calculate_confidence(entries, "Tell me about the MS CS program")

    assert result["level"] == "High"
    assert result["needs_verification"] is False


def test_confidence_requires_verification_for_deadline():
    entries = [
        DummyEntry(
            topic="Old conversation answer",
            content="Deadline may be in March.",
            source_type="conversation",
            confidence=0.6,
        )
    ]

    result = calculate_confidence(entries, "What is the application deadline?")

    assert result["needs_verification"] is True
    assert result["level"] in ["Low", "Medium"]


def test_low_confidence_when_no_evidence():
    result = calculate_confidence([], "What is the application deadline?")

    assert result["level"] == "Low"
    assert result["needs_verification"] is True


def test_trust_summary_has_sources():
    entries = [
        DummyEntry(
            topic="AFRL Connection",
            content="Wright State has AFRL proximity.",
            source_type="seed",
            confidence=1.0,
        )
    ]

    summary = build_trust_summary(entries, "Tell me about AFRL")

    assert "confidence" in summary
    assert "sources" in summary
    assert summary["sources"][0]["topic"] == "AFRL Connection"