# knowledge/trust_layer.py
"""
Evidence-Aware Recommendation / Advisor Trust Layer.

Purpose:
- Check how reliable a recommendation/fact is.
- Detect high-risk university facts like deadlines, tuition, GRE, funding.
- Give university agents internal guidance.
- Keep Aria's final answer human, not robotic.
"""

from __future__ import annotations

from typing import Any, Dict, List


HIGH_RISK_FACT_KEYWORDS = [
    "deadline",
    "application deadline",
    "last date",
    "due date",
    "tuition",
    "fees",
    "fee",
    "cost",
    "cost of attendance",
    "credit hour",
    "per credit",
    "gre",
    "gmat",
    "gpa requirement",
    "minimum gpa",
    "toefl",
    "ielts",
    "duolingo",
    "english proficiency",
    "acceptance rate",
    "admit rate",
    "admission rate",
    "salary",
    "placement rate",
    "job placement",
    "funding",
    "assistantship",
    "ra",
    "ta",
    "stipend",
    "fellowship",
    "scholarship",
    "application fee",
    "i-20",
    "visa",
    "cpt",
    "opt",
    "sevis",
    "international student",
]


def _get(entry: Any, key: str, default=None):
    """
    Supports both:
    1. KnowledgeEntry object
    2. dict-based persistent memory entry
    """
    if isinstance(entry, dict):
        return entry.get(key, default)

    return getattr(entry, key, default)


def _safe_float(value: Any, default: float = 0.5) -> float:
    """Convert confidence-like values safely and clamp between 0 and 1."""
    try:
        if value in [None, ""]:
            return default

        score = float(value)
        return max(0.0, min(1.0, score))
    except (TypeError, ValueError):
        return default


def _normalize(text: Any) -> str:
    """Normalize text for keyword checks and simple matching."""
    return " ".join(str(text or "").lower().strip().split())


def is_high_risk_question(question: str) -> bool:
    """
    High-risk questions are questions where guessing is dangerous.

    Examples:
    - deadlines
    - tuition and fees
    - funding
    - GRE/GPA rules
    - TOEFL/IELTS rules
    - visa/CPT/OPT details
    - acceptance rates or salary outcomes
    """
    q = _normalize(question)
    return any(keyword in q for keyword in HIGH_RISK_FACT_KEYWORDS)


def source_weight(source_type: str) -> float:
    """
    Assign reliability weight by source type.

    Higher value means the source should count more toward confidence.
    """
    source_type = _normalize(source_type)

    if source_type in ["human_verified", "verified", "official_human"]:
        return 1.0

    if source_type in ["scraped", "official_page", "official"]:
        return 0.9

    if source_type == "seed":
        return 0.85

    if source_type in ["human", "manual"]:
        return 0.9

    if source_type == "conversation":
        return 0.55

    if source_type in ["fallback_scrape", "page_summary"]:
        return 0.5

    return 0.4


def _confidence_level(score: float, high_risk: bool) -> str:
    """Return Low/Medium/High label using stricter cutoffs for high-risk facts."""
    if high_risk:
        if score >= 0.9:
            return "High"
        if score >= 0.40:
            return "Medium"
        return "Low"

    if score >= 0.85:
        return "High"
    if score >= 0.40:
        return "Medium"
    return "Low"


def calculate_confidence(entries: List[Any], question: str) -> Dict:
    """
    Calculate a simple confidence label using:
    - number of relevant entries
    - stored confidence
    - source type reliability
    - whether the question is high-risk
    """
    high_risk = is_high_risk_question(question)

    if not entries:
        return {
            "level": "Low",
            "score": 0.0,
            "needs_verification": True,
            "reason": "No directly relevant verified evidence was found.",
        }

    weighted_scores = []

    for entry in entries:
        stored_confidence = _safe_float(_get(entry, "confidence", 0.5), 0.5)
        source_type = _get(entry, "source_type", "unknown")
        weighted_scores.append(stored_confidence * source_weight(source_type))

    if not weighted_scores:
        score = 0.0
    else:
        score = sum(weighted_scores) / len(weighted_scores)

    score = round(max(0.0, min(1.0, score)), 2)
    level = _confidence_level(score, high_risk)

    # High-risk facts should almost always be verified before a student acts.
    if high_risk:
        if level == "High":
            return {
                "level": "High",
                "score": score,
                "needs_verification": True,
                "reason": (
                    "The answer has strong support, but this is a changing or high-risk "
                    "university fact and should still be verified on the official page."
                ),
            }

        if level == "Medium":
            return {
                "level": "Medium",
                "score": score,
                "needs_verification": True,
                "reason": (
                    "Some relevant evidence exists, but this is a changing university fact "
                    "such as deadline, tuition, funding, GRE/GPA rule, visa detail, or admission statistic."
                ),
            }

        return {
            "level": "Low",
            "score": score,
            "needs_verification": True,
            "reason": "Evidence is weak for a high-risk factual question.",
        }

    # Normal non-high-risk questions.
    if level == "High":
        return {
            "level": "High",
            "score": score,
            "needs_verification": False,
            "reason": "The answer is supported by strong relevant knowledge entries.",
        }

    if level == "Medium":
        return {
            "level": "Medium",
            "score": score,
            "needs_verification": False,
            "reason": "The answer has reasonable support, but should be phrased carefully.",
        }

    return {
        "level": "Low",
        "score": score,
        "needs_verification": True,
        "reason": "The available evidence is weak or mostly conversation-learned.",
    }


def build_evidence_context(entries: List[Any]) -> str:
    """
    Convert entries into internal prompt context for a university agent.

    This is intended for internal LLM prompt context, not direct student display.
    """
    if not entries:
        return "No directly relevant verified evidence was found in the knowledge base."

    lines = []

    for index, entry in enumerate(entries, start=1):
        topic = _get(entry, "topic", "Untitled")
        content = _get(entry, "content", "")
        source_type = _get(entry, "source_type", "unknown")
        source_url = _get(entry, "source_url", None)
        confidence = _get(entry, "confidence", 0.5)

        lines.append(
            f"EVIDENCE {index}\n"
            f"Topic: {topic}\n"
            f"Content: {content}\n"
            f"Source type: {source_type}\n"
            f"Source URL: {source_url or 'Not available'}\n"
            f"Stored confidence: {confidence}\n"
        )

    return "\n".join(lines)


def build_trust_summary(entries: List[Any], question: str) -> Dict:
    """
    Build structured trust metadata.

    Use this in reports/debug logs/internal enrichment, not raw in normal chat.
    """
    confidence = calculate_confidence(entries, question)

    sources = []

    for entry in entries:
        sources.append(
            {
                "topic": _get(entry, "topic", "Untitled"),
                "source_type": _get(entry, "source_type", "unknown"),
                "source_url": _get(entry, "source_url", None),
                "confidence": _get(entry, "confidence", 0.5),
            }
        )

    return {
        "confidence": confidence,
        "sources": sources,
        "high_risk_question": is_high_risk_question(question),
    }


def human_trust_instruction(trust_summary: Dict) -> str:
    """
    Convert trust metadata into natural style guidance.

    This prevents robotic output and keeps Aria's final answer human.
    """
    confidence = trust_summary.get("confidence", {})
    level = str(confidence.get("level", "Low")).title()
    needs_verification = bool(confidence.get("needs_verification", True))

    if level == "High" and not needs_verification:
        return (
            "You can sound reasonably confident, but not arrogant. "
            "Do not expose raw confidence scores, source weights, or metadata."
        )

    if level == "High" and needs_verification:
        return (
            "You can say the evidence looks strong, but because this information can change, "
            "recommend verifying the latest official page before the student acts on it. "
            "Do not expose raw confidence scores or metadata."
        )

    if level == "Medium":
        return (
            "Use careful human wording such as 'this looks likely', 'I would treat this as', "
            "'I am reasonably confident', or 'I would still verify this'. "
            "Do not expose raw confidence scores or metadata."
        )

    return (
        "Be cautious. Say the current knowledge base does not have enough verified information. "
        "Do not invent facts. Suggest checking the official university page or asking admissions."
    )


def should_create_pending_query(trust_summary: Dict) -> bool:
    """
    Decide whether a low-confidence answer should become a pending university query.
    """
    confidence = trust_summary.get("confidence", {})
    level = str(confidence.get("level", "Low")).title()
    needs_verification = bool(confidence.get("needs_verification", True))
    high_risk = bool(trust_summary.get("high_risk_question", False))

    if level == "Low":
        return True

    if high_risk and needs_verification and level != "High":
        return True

    return False
