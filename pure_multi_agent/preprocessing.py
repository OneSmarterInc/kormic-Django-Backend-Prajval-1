# pure_multi_agent/preprocessing.py
# Deterministic pre/post steps that ran unconditionally *before* the old
# intent classifier in agents.student_agent.StudentAgent.chat() -- they were
# never part of the hardcoded routing chain the user wants replaced, so they
# stay as plain function calls around the graph invocation rather than graph
# nodes. Ports StudentAgent's roadmap shortcuts, automatic profile-field
# extraction, and memory bookkeeping verbatim.

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

import anthropic
from rich.console import Console

try:
    from roadmap.roadmap_planner import RoadmapPlanner
except Exception:
    RoadmapPlanner = None

from pure_multi_agent.tracing import VERBOSE

console = Console()
client = anthropic.Anthropic()

MODEL = "claude-haiku-4-5-20251001"

_roadmap_planner = RoadmapPlanner() if RoadmapPlanner is not None else None

UNIVERSITY_ALIASES = {
    "cmu": "CMU",
    "carnegie mellon": "CMU",
    "mit": "MIT",
    "wright state": "Wright State",
    "msu": "Michigan State",
    "michigan state": "Michigan State",
    "uw": "University of Washington",
    "university of washington": "University of Washington",
    "rutgers": "Rutgers",
    "sdsu": "San Diego State",
    "franklin": "Franklin University",
    "franklin university": "Franklin University",
}

IMPORTANT_MEMORY_KEYWORDS = [
    "gpa", "budget", "cmu", "mit", "wright state", "funding",
    "sop", "gre", "toefl", "ielts", "research", "work experience",
    "github", "linkedin", "ai", "ml", "data science", "cybersecurity",
    "software engineering", "deadline", "scholarship",
]

PROFILE_UPDATE_KEYWORDS = [
    "my gpa", "gpa is", "gre", "toefl", "ielts", "budget",
    "graduation", "graduate in", "graduated", "major", "institution",
    "college", "university", "skills", "project", "research",
    "work experience", "internship", "github", "i studied",
    "i have", "i am from", "my name is", "program",
]


def roadmap_shortcut(ctx: Dict[str, Any], user_message: str) -> Optional[str]:
    """Roadmap progress/generation shortcuts -- cheap and unambiguous, so they
    ran before any LLM-based routing in the original code too. Returns a
    response string to short-circuit the turn with, or None to continue."""
    student_profile = ctx["student_profile"]
    lower_msg = user_message.lower()

    roadmap = student_profile.get("roadmap")
    if roadmap and "progress" in lower_msg:
        if VERBOSE:
            console.print("[dim]pre-check: roadmap progress shortcut matched -- skipping the agent graph[/dim]")
        return (
            f"You are currently on Month {roadmap.get('current_month', 'N/A')} "
            f"of your {str(roadmap.get('exam', 'application')).upper()} roadmap.\n\n"
            f"Status: {roadmap.get('status', 'Not available')}"
        )

    if (
        _roadmap_planner is not None
        and hasattr(_roadmap_planner, "is_roadmap_request")
        and _roadmap_planner.is_roadmap_request(user_message)
    ):
        if VERBOSE:
            console.print("[dim]pre-check: roadmap generation request matched -- skipping the agent graph[/dim]")
        roadmap_response = _roadmap_planner.generate_application_roadmap(
            student_profile,
            user_message,
        )

        if isinstance(roadmap_response, dict):
            student_profile["roadmap"] = roadmap_response
            return json.dumps(roadmap_response, indent=2, ensure_ascii=False)

        return str(roadmap_response)

    return None


def _should_extract_profile_information(user_message: str) -> bool:
    """Avoid spending an LLM call on every casual message."""
    lower = user_message.lower()
    return any(keyword in lower for keyword in PROFILE_UPDATE_KEYWORDS)


def _clean_extracted_profile(extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Remove empty values before merging into the student profile."""
    cleaned: Dict[str, Any] = {}

    for key, value in (extracted or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        cleaned[key] = value

    return cleaned


def _extract_profile_information(user_message: str) -> Dict[str, Any]:
    """Extract structured profile updates from a natural-language message."""
    prompt = f"""
You are an AI profile extractor.

Return ONLY valid JSON. Do not explain anything.

Extract profile information from the student's message. If a field is not present,
omit it or leave it empty.

Possible fields:
{{
  "name": "",
  "institution": "",
  "major": "",
  "program": "",
  "gpa": null,
  "gre_quant": null,
  "gre_verbal": null,
  "toefl": null,
  "ielts": null,
  "budget": null,
  "graduation_year": null,
  "work_months": null,
  "research": "",
  "skills": [],
  "projects": [],
  "github": {{}}
}}

Student message:
{user_message}
"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()

        if text.startswith("```"):
            text = text.replace("```json", "")
            text = text.replace("```", "").strip()

        data = json.loads(text)
        if not isinstance(data, dict):
            return {}

        return _clean_extracted_profile(data)

    except Exception as exc:
        console.print(f"[yellow]Profile extraction skipped: {exc}[/yellow]")
        return {}


def extract_profile_information(ctx: Dict[str, Any], user_message: str) -> None:
    """Automatically merge clear profile facts shared mid-chat, same
    unconditional keyword-gated pre-check as the original StudentAgent."""
    if not _should_extract_profile_information(user_message):
        return

    if VERBOSE:
        console.print("[dim]pre-check: profile-update keywords detected -- extracting structured fields...[/dim]")

    extracted = _extract_profile_information(user_message)

    if extracted:
        ctx["student_profile"].update(extracted)
        if VERBOSE:
            console.print(f"[green]pre-check: profile updated from chat -- {list(extracted.keys())}[/green]")


def update_memory(ctx: Dict[str, Any], user_message: str, reply: str) -> None:
    """Ports StudentAgent.update_memory verbatim: tracks important points and
    university-alias mentions in the durable per-student memory dict."""
    memory = ctx["memory"]
    text = user_message.lower()

    if any(keyword in text for keyword in IMPORTANT_MEMORY_KEYWORDS):
        point = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": user_message,
            "aria": (reply or "")[:500],
        }

        memory["important_points"].append(point)
        memory["important_points"] = memory["important_points"][-50:]

    for alias, canonical_name in UNIVERSITY_ALIASES.items():
        if alias in text and canonical_name not in memory["universities_discussed"]:
            memory["universities_discussed"].append(canonical_name)
