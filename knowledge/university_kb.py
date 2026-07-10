# knowledge/university_kb.py
# Knowledge base for university agents.
# Runs in memory by default.
# Every fact has a source type: 'seed', 'scraped', 'conversation', or 'human_verified'.

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


class KnowledgeEntry:
    def __init__(
        self,
        topic: str,
        content: str,
        source_type: str,
        source_url: Optional[str] = None,
        confidence: float = 1.0,
        learned_at: Optional[str] = None,
        times_used: int = 0,
    ):
        self.topic = str(topic or "").strip()
        self.content = str(content or "").strip()
        self.source_type = str(source_type or "unknown").strip().lower()
        self.source_url = source_url
        self.confidence = self._safe_confidence(confidence)
        self.learned_at = learned_at or datetime.now().isoformat()
        self.times_used = int(times_used or 0)
        self.search_score = 0.0

    def _safe_confidence(self, value: Any) -> float:
        try:
            score = float(value)
            return max(0.0, min(1.0, score))
        except (TypeError, ValueError):
            return 0.5

    def to_dict(self) -> Dict[str, Any]:
        """Serialize entry for optional future persistence."""
        return {
            "topic": self.topic,
            "content": self.content,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "learned_at": self.learned_at,
            "times_used": self.times_used,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeEntry":
        """Create an entry from a dictionary."""
        return cls(
            topic=data.get("topic", ""),
            content=data.get("content", ""),
            source_type=data.get("source_type", "unknown"),
            source_url=data.get("source_url"),
            confidence=data.get("confidence", 1.0),
            learned_at=data.get("learned_at"),
            times_used=data.get("times_used", 0),
        )

    def __repr__(self) -> str:
        return f"[{self.source_type.upper()}] {self.topic}: {self.content[:80]}..."


class UniversityKnowledgeBase:
    """
    Knowledge base for one university agent.

    Stores:
    - seed facts
    - scraped facts
    - conversation facts
    - human-verified answers

    This version stays in memory but includes to_dict/from_dict helpers for
    future persistence if needed.
    """

    STOP_WORDS = {
        "what", "is", "are", "the", "for", "of", "to", "a", "an",
        "does", "do", "can", "have", "has", "at", "in", "on",
        "and", "or", "with", "about", "tell", "me", "please",
        "give", "show", "explain", "know", "need", "want",
        "wright", "state", "university", "franklin",
    }

    IMPORTANT_WORDS = {
        "toefl",
        "ielts",
        "duolingo",
        "gre",
        "gmat",
        "gpa",
        "deadline",
        "application",
        "requirements",
        "requirement",
        "tuition",
        "fees",
        "fee",
        "cost",
        "credit",
        "funding",
        "research",
        "scholarship",
        "assistantship",
        "stipend",
        "admission",
        "international",
        "placement",
        "salary",
        "faculty",
        "coordinator",
        "lab",
        "internship",
        "employment",
        "career",
        "visa",
        "i20",
        "i-20",
        "cpt",
        "opt",
        "sevis",
        "online",
        "duration",
        "credits",
        "course",
        "courses",
        "cybersecurity",
        "data",
        "analytics",
        "software",
        "systems",
        "computer",
        "science",
        "engineering",
    }

    SOURCE_PRIORITY = {
        "human_verified": 1.6,
        "verified": 1.5,
        "seed": 1.3,
        "scraped": 1.15,
        "official_page": 1.15,
        "conversation": 0.85,
        "fallback_scrape": 0.55,
        "unknown": 0.5,
    }

    def __init__(self, university_id: str):
        self.university_id = university_id
        self.entries: List[KnowledgeEntry] = []
        self.total_questions_answered = 0

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _normalize_key(self, topic: str, content: str) -> tuple[str, str]:
        topic_key = " ".join(str(topic or "").lower().split())
        content_key = " ".join(str(content or "").lower().split())[:500]
        return topic_key, content_key

    def _find_duplicate(self, topic: str, content: str) -> Optional[KnowledgeEntry]:
        new_key = self._normalize_key(topic, content)

        for entry in self.entries:
            if self._normalize_key(entry.topic, entry.content) == new_key:
                return entry

        return None

    def store(
        self,
        topic: str,
        content: str,
        source_type: str,
        source_url: Optional[str] = None,
        confidence: float = 1.0,
        allow_duplicates: bool = False,
    ) -> KnowledgeEntry:
        """
        Add a new knowledge entry.

        If the same topic/content already exists, update confidence/source metadata
        instead of adding duplicates.
        """
        topic = str(topic or "").strip()
        content = str(content or "").strip()

        if not topic or not content:
            raise ValueError("Knowledge entry requires both topic and content.")

        if not allow_duplicates:
            duplicate = self._find_duplicate(topic, content)

            if duplicate:
                try:
                    duplicate.confidence = max(duplicate.confidence, float(confidence))
                except Exception:
                    pass

                if source_url and not duplicate.source_url:
                    duplicate.source_url = source_url

                # Prefer stronger source types.
                old_priority = self.SOURCE_PRIORITY.get(duplicate.source_type, 0.5)
                new_priority = self.SOURCE_PRIORITY.get(str(source_type or "").lower(), 0.5)

                if new_priority > old_priority:
                    duplicate.source_type = str(source_type or "unknown").lower()

                return duplicate

        entry = KnowledgeEntry(
            topic=topic,
            content=content,
            source_type=source_type,
            source_url=source_url,
            confidence=confidence,
        )

        self.entries.append(entry)
        return entry

    def store_bulk(self, facts: List[Dict[str, Any]]) -> int:
        """Store multiple facts at once and return count stored/updated."""
        count = 0

        for fact in facts or []:
            if not isinstance(fact, dict):
                continue

            try:
                self.store(
                    topic=fact.get("topic", ""),
                    content=fact.get("content", ""),
                    source_type=fact.get("source_type", "seed"),
                    source_url=fact.get("source_url"),
                    confidence=fact.get("confidence", 1.0),
                )
                count += 1
            except ValueError:
                continue

        return count

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> List[str]:
        text = str(text or "").lower()

        for ch in "?,.!:;()[]{}\"'/|":
            text = text.replace(ch, " ")

        return [
            word.strip()
            for word in text.split()
            if word.strip() and word.strip() not in self.STOP_WORDS
        ]

    def _phrase_score(self, query: str, topic: str, content: str) -> float:
        query_clean = " ".join(str(query or "").lower().split())
        topic_clean = " ".join(str(topic or "").lower().split())
        content_clean = " ".join(str(content or "").lower().split())

        score = 0.0

        if query_clean and query_clean in topic_clean:
            score += 15

        if query_clean and query_clean in content_clean:
            score += 8

        return score

    def search(self, query: str, limit: int = 8) -> List[KnowledgeEntry]:
        """
        Improved keyword search.

        Uses:
        - stop word removal
        - important keyword boosting
        - exact phrase boosting
        - topic/content scoring
        - confidence score
        - source priority
        - usage score
        """
        query_words = self._tokenize(query)

        if not query_words:
            return []

        matches: List[KnowledgeEntry] = []

        for entry in self.entries:
            score = self._phrase_score(query, entry.topic, entry.content)

            topic = entry.topic.lower()
            content = entry.content.lower()

            for word in query_words:
                if word in topic:
                    score += 10 if word in self.IMPORTANT_WORDS else 3

                if word in content:
                    score += 5 if word in self.IMPORTANT_WORDS else 1

            source_boost = self.SOURCE_PRIORITY.get(entry.source_type, 0.5)

            score *= entry.confidence
            score *= source_boost
            score += entry.times_used * 0.1

            if score > 0:
                entry.search_score = round(score, 4)
                matches.append(entry)

        matches.sort(key=lambda entry: entry.search_score, reverse=True)

        results = matches[:limit]

        for entry in results:
            entry.times_used += 1

        return results

    # ------------------------------------------------------------------
    # Context / stats
    # ------------------------------------------------------------------

    def get_full_context(self, max_entries: int = 60) -> str:
        """
        Return formatted knowledge base context for the agent prompt.

        Prioritizes:
        - human-verified facts
        - high-confidence entries
        - frequently used entries
        - stronger source types
        """
        sorted_entries = sorted(
            self.entries,
            key=lambda entry: (
                self.SOURCE_PRIORITY.get(entry.source_type, 0.5) * 2
                + entry.confidence * 2
                + entry.times_used * 0.5
            ),
            reverse=True,
        )[:max_entries]

        if not sorted_entries:
            return "Knowledge base is empty — agent is learning."

        lines = ["KNOWLEDGE BASE:"]

        for entry in sorted_entries:
            source_label = {
                "seed": "VERIFIED SEED",
                "scraped": "FROM WEBSITE",
                "official_page": "FROM OFFICIAL PAGE",
                "conversation": "LEARNED",
                "human_verified": "HUMAN VERIFIED",
                "verified": "VERIFIED",
                "fallback_scrape": "SCRAPED SUMMARY",
            }.get(entry.source_type, entry.source_type.upper())

            source_url_text = f" Source: {entry.source_url}" if entry.source_url else ""

            lines.append(
                f"[{source_label}] {entry.topic}: {entry.content}"
                f" (confidence: {entry.confidence}){source_url_text}"
            )

        lines.append(
            f"\n(Knowledge base contains {len(self.entries)} entries. "
            f"{self.total_questions_answered} questions answered so far.)"
        )

        return "\n".join(lines)

    def stats(self) -> Dict[str, Any]:
        source_counts: Dict[str, int] = {}

        for entry in self.entries:
            source_counts[entry.source_type] = source_counts.get(entry.source_type, 0) + 1

        return {
            "university_id": self.university_id,
            "total_entries": len(self.entries),
            "by_source": source_counts,
            "questions_answered": self.total_questions_answered,
        }

    # ------------------------------------------------------------------
    # Optional serialization helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize knowledge base for optional future persistence."""
        return {
            "university_id": self.university_id,
            "total_questions_answered": self.total_questions_answered,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UniversityKnowledgeBase":
        """Load a knowledge base from a dictionary."""
        kb = cls(data.get("university_id", "unknown"))
        kb.total_questions_answered = int(data.get("total_questions_answered", 0) or 0)

        for entry_data in data.get("entries", []):
            if isinstance(entry_data, dict):
                kb.entries.append(KnowledgeEntry.from_dict(entry_data))

        return kb
