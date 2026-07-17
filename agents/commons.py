# agents/commons.py
# The Korgut Commons — where agents live and communicate.
#
# This is the single orchestration layer every backend agent (university
# agents, verification, fit assessment) is reached through. The student
# never talks to any of these agents directly -- only their personal
# StudentAgent does, via the functions in this module. This module also
# owns the lazy-build/in-process-cache lifecycle for every agent instance
# (previously split across module-level dicts in django_api/views.py).

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import anthropic
from rich.console import Console

console = Console()

# The Commons registry — all active university agents register here.
_university_agents: Dict[str, Any] = {}

# In-process caches for the other lazily-built, session-scoped agents.
# Rebuilt from persisted DB state on first use per worker process --
# same lifecycle/caveats as _university_agents (not shared across workers,
# not durable across restarts; only the underlying DB rows are durable).
_student_agents: Dict[str, Any] = {}
_profile_presenters: Dict[str, Any] = {}


def _get_anthropic_client() -> anthropic.Anthropic:
    """
    Create Anthropic client only when synthesis is required.

    This avoids failing during app startup if the synthesis layer is not used
    immediately, while still requiring ANTHROPIC_API_KEY when synthesise() runs.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY not found. Add it to your .env file before using synthesis."
        )

    return anthropic.Anthropic()


# ---------------------------------------------------------------------
# University agent registry
# ---------------------------------------------------------------------

def register(university_id: str, agent: Any) -> None:
    """Register a university agent in the Commons."""
    _university_agents[university_id] = agent

    agent_name = getattr(agent, "persona", {}).get("agent_name", university_id)

    console.print(
        f"[dim]Commons: {agent_name} registered as {university_id}.[/dim]"
    )


def unregister(university_id: str) -> bool:
    """Remove a university agent from the Commons if it exists."""
    if university_id in _university_agents:
        del _university_agents[university_id]
        console.print(f"[dim]Commons: {university_id} unregistered.[/dim]")
        return True

    return False


def get_agent(university_id: str) -> Optional[Any]:
    """
    Return a registered university agent without asking it a question, and
    without lazily building one. Use get_university_agent() instead when the
    caller wants a usable agent whether or not it's been built yet.
    """
    return _university_agents.get(university_id)


def list_agents() -> List[str]:
    """Return IDs of all registered university agents."""
    return list(_university_agents.keys())


# University directory (single source of truth -- replaces the three
# separately-hardcoded university id/keyword lists that used to live in
# django_api/views.py's api_home and agents/student_agent.py)

def list_university_ids() -> List[str]:
    """Every known university id, in persona-definition order."""
    from personas.university_personas import UNIVERSITY_PERSONAS

    return list(UNIVERSITY_PERSONAS.keys())


def match_university_ids(text: str) -> List[str]:
    """Return every university id whose configured keywords appear in text."""
    from personas.university_personas import UNIVERSITY_PERSONAS

    lower = (text or "").lower()
    matched = []

    for university_id, persona in UNIVERSITY_PERSONAS.items():
        keywords = persona.get("keywords", [])
        if any(keyword in lower for keyword in keywords):
            matched.append(university_id)

    return matched


# ---------------------------------------------------------------------
# Lazy agent construction + caching
# ---------------------------------------------------------------------

def get_university_agent(university_id: str, auto_scrape: Optional[bool] = None) -> Any:
    """
    Return the cached university agent, building and registering it on
    first use. Raises ValueError for an unknown university_id.
    """
    if university_id in _university_agents:
        return _university_agents[university_id]

    from agents.university_agent import UniversityAgent
    from personas.university_personas import UNIVERSITY_PERSONAS

    if university_id not in UNIVERSITY_PERSONAS:
        raise ValueError(f"Unknown university_id: {university_id}")

    if auto_scrape is None:
        auto_scrape = os.getenv("KORGUT_AUTO_SCRAPE", "false").lower() == "true"

    agent = UniversityAgent(university_id, auto_scrape=auto_scrape)
    register(university_id, agent)

    return agent


def get_student_agent(student_id: str) -> Any:
    """
    Return the cached personal agent for this student, building it (and
    assigning a default agent_name if the student doesn't have one yet) on
    first use.
    """
    from agents.agent_identity import ensure_agent_name
    from agents.student_agent import StudentAgent
    from django_api.models import StudentProfile
    from django_api.services import load_profile_data, make_student_id

    key = make_student_id(student_id)

    if key in _student_agents:
        return _student_agents[key]

    profile_row, _ = StudentProfile.objects.get_or_create(student_id=key)
    agent_name = ensure_agent_name(profile_row)

    profile_data = load_profile_data(student_id)
    _student_agents[key] = StudentAgent(profile_data, student_id=key, agent_name=agent_name)

    return _student_agents[key]


def drop_student_agent(student_id: str) -> None:
    """Evict a cached personal agent, e.g. right after its agent_name changes.

    Also evicts the LangGraph runtime's cached per-student context
    (pure_multi_agent/runtime.py), which is what the chat API actually uses
    now -- this keeps AgentNameAPIView's rename flow working unchanged."""
    from django_api.services import make_student_id
    from pure_multi_agent.runtime import drop_student_context

    _student_agents.pop(make_student_id(student_id), None)
    drop_student_context(student_id)


def get_profile_presenter(university_id: str) -> Any:
    """Return the cached officer-facing ProfilePresenterAgent for a university."""
    if university_id in _profile_presenters:
        return _profile_presenters[university_id]

    from agents.profile_presenter import ProfilePresenterAgent

    _profile_presenters[university_id] = ProfilePresenterAgent(university_id)
    return _profile_presenters[university_id]


# ---------------------------------------------------------------------
# University querying / synthesis (used by the student's personal agent)
# ---------------------------------------------------------------------

def query(
    university_id: str,
    question: str,
    student_context: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """
    Query a specific university agent, building it on demand.

    Called by the student's personal agent when it needs verified
    information about a program.
    """
    try:
        agent = get_university_agent(university_id)
    except ValueError:
        console.print(f"[yellow]No agent available for {university_id}.[/yellow]")
        return None

    try:
        return agent.answer(question, student_context)
    except Exception as exc:
        console.print(f"[yellow]Query failed for {university_id}: {exc}[/yellow]")
        return {
            "agent_name": getattr(agent, "persona", {}).get("agent_name", university_id),
            "university": getattr(agent, "persona", {}).get("university", university_id),
            "answer": "I could not answer this because the university agent hit an error.",
            "error": str(exc),
        }


def query_all(
    question: str,
    student_context: Optional[dict] = None,
    university_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Broadcast a question to every university agent (or a specific subset),
    building any that aren't cached yet. Useful for cross-program
    comparisons and "which university fits me" style questions.
    """
    responses: List[Dict[str, Any]] = []
    target_ids = university_ids if university_ids is not None else list_university_ids()

    for university_id in target_ids:
        try:
            agent = get_university_agent(university_id)
            response = agent.answer(question, student_context)
            if response:
                responses.append(response)
        except Exception as exc:
            console.print(f"[yellow]Query failed for {university_id}: {exc}[/yellow]")
            responses.append(
                {
                    "agent_name": university_id,
                    "university": university_id,
                    "answer": "This agent could not answer because it hit an error.",
                    "error": str(exc),
                }
            )

    return responses


def synthesise(
    original_question: str,
    responses: List[Dict[str, Any]],
    student_profile: dict,
) -> str:
    """
    When the student's personal agent receives answers from multiple
    university agents, synthesise them into one clear, personalised answer.
    """
    valid_responses = [
        response
        for response in responses
        if response and response.get("answer")
    ]

    if not valid_responses:
        return "I wasn't able to get answers from the university agents on that one."

    compiled = "\n\n".join(
        [
            (
                f"{response.get('agent_name', 'University Agent')} "
                f"({response.get('university', 'Unknown University')}) says:\n"
                f"{response.get('answer', '')}"
            )
            for response in valid_responses
        ]
    )

    student_name = student_profile.get("name", "the student")

    synthesis_prompt = f"""You are the student's personal advising agent. Multiple
university agents in the Korgut Commons have answered a question in the
background. Synthesise their responses into a single clear, personalised
answer for {student_name}.

Be specific. Cite university names where relevant. If the agents gave
different answers, note the differences clearly. Keep your answer
conversational and direct. Do not mention that you consulted separate
"agents" mechanically -- speak as the student's one advisor who checked
with each program.

ORIGINAL QUESTION:
{original_question}

UNIVERSITY AGENT RESPONSES:
{compiled}
"""

    try:
        client = _get_anthropic_client()

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": synthesis_prompt}],
        )

        return response.content[0].text

    except Exception as exc:
        console.print(f"[yellow]Synthesis failed: {exc}[/yellow]")

        # Fallback: return combined agent answers instead of crashing.
        return (
            "I could not synthesise the responses automatically, but here are the "
            "university agent answers:\n\n"
            + compiled
        )


# ---------------------------------------------------------------------
# Verification (used by the student's personal agent -- there is no
# direct student-facing "reanalyze"/"decision" endpoint anymore; those
# actions only happen via chat, routed through here)
# ---------------------------------------------------------------------

def run_verification(student_id: str, user: Any = None) -> Dict[str, Any]:
    from verification.services import run_verification as _run_verification

    return _run_verification(student_id, user=user)


def list_verification_items(student_id: str, filter_status: str = "open") -> Dict[str, Any]:
    from verification.services import list_items

    return list_items(student_id, filter_status)


def resolve_verification_item(*, student_id: str, item_id: int, action: str, note: str = "") -> Dict[str, Any]:
    from verification.services import resolve_item

    return resolve_item(student_id=student_id, item_id=item_id, action=action, note=note)


# ---------------------------------------------------------------------
# Fit assessment (used by the student's personal agent -- moved from
# django_api/views.generate_fit_assessment, which is no longer a direct
# student-facing POST endpoint)
# ---------------------------------------------------------------------

def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _fallback_fit_assessment(profile: Dict[str, Any], university_id: str, agent: Optional[Any] = None) -> Dict[str, Any]:
    """Deterministic fit estimate used when the AI assessment fails or is
    unusable -- keeps the student agent able to answer even if the model
    call errors out."""
    gpa = _safe_number(profile.get("gpa"), 0)
    gpa_scale = _safe_number(profile.get("gpa_scale"), 10)
    gre_quant = _safe_number(profile.get("gre_quant"), 0)
    toefl = _safe_number(profile.get("toefl"), 0)
    ielts = _safe_number(profile.get("ielts"), 0)
    budget = _safe_number(profile.get("budget"), 0)
    major = str(profile.get("major", "")).lower()
    program = str(profile.get("program", "")).lower()
    research = str(profile.get("research", "")).lower()

    score = 45
    strengths: List[str] = []
    gaps: List[str] = []
    gpa_percent = gpa / gpa_scale if gpa_scale > 0 else 0

    if gpa_percent >= 0.80:
        score += 15
        strengths.append("Strong academic profile based on GPA.")
    elif gpa_percent >= 0.70:
        score += 8
        strengths.append("Decent academic profile.")
    else:
        gaps.append("GPA may need stronger support through projects, GRE, or experience.")

    if "computer" in major or "cs" in major or "computer" in program or "science" in program or "software" in program:
        score += 12
        strengths.append("Academic background aligns with Computer Science.")
    else:
        gaps.append("Program background alignment should be explained clearly.")

    if gre_quant >= 165:
        score += 10
        strengths.append("Strong GRE Quant score.")
    elif gre_quant >= 160:
        score += 6
        strengths.append("Good GRE Quant score.")
    elif gre_quant > 0:
        gaps.append("GRE Quant score may be moderate for CS programs.")
    else:
        gaps.append("GRE score is missing or not provided.")

    if toefl >= 90:
        score += 8
        strengths.append("TOEFL score looks acceptable for many graduate programs.")
    elif ielts >= 6.5:
        score += 8
        strengths.append("IELTS score looks acceptable for many graduate programs.")
    elif toefl > 0 or ielts > 0:
        gaps.append("English proficiency score should be verified against the university minimum.")
    else:
        gaps.append("English proficiency score is missing.")

    if any(word in research for word in ["ai", "ml", "web", "project", "research", "internship"]):
        score += 8
        strengths.append("Projects/research experience supports the application.")
    else:
        gaps.append("More project or research detail would improve the profile.")

    if budget >= 40000:
        score += 7
        strengths.append("Budget appears reasonable for many US graduate options, but tuition must be verified.")
    elif budget >= 25000:
        score += 3
        gaps.append("Budget may need careful planning depending on tuition and living cost.")
    else:
        gaps.append("Budget may be tight for US graduate study.")

    from personas.university_personas import UNIVERSITY_PERSONAS

    persona = UNIVERSITY_PERSONAS.get(university_id, {})
    university_name = persona.get("name", university_id)
    agent_name = persona.get("agent_name") or (getattr(agent, "agent_name", university_id) if agent else university_id)
    if university_id == "franklin_cs":
        score += 3

    score = max(0, min(100, int(score)))

    if score >= 80:
        match_tier = "strong"
        recommendation = "recommend"
        realistic = True
    elif score >= 65:
        match_tier = "target"
        recommendation = "recommend"
        realistic = True
    elif score >= 50:
        match_tier = "possible"
        recommendation = "consider"
        realistic = True
    else:
        match_tier = "reach"
        recommendation = "consider"
        realistic = False

    return {
        "match_tier": match_tier,
        "match_score": score,
        "fit_summary": f"Based on the available profile, this looks like a {match_tier} fit for {university_name}.",
        "strengths_for_program": strengths or ["Basic profile information is available for assessment."],
        "gaps_for_program": gaps or ["Verify official requirements on the university website."],
        "recommendation": recommendation,
        "realistic": realistic,
        "specific_advice": "Verify tuition, deadlines, GRE/TOEFL, and funding before final decision.",
        "university": university_name,
        "agent": agent_name,
        "assessment_source": "api_fallback",
    }


def _assessment_failed(assessment: Any) -> bool:
    if not isinstance(assessment, dict):
        return True
    if assessment.get("match_tier") == "unknown":
        return True
    if int(assessment.get("match_score") or 0) <= 0:
        return True
    return False


def generate_fit_assessment(student_id: str, university_id: str, force: bool = False) -> Dict[str, Any]:
    """
    Generate (or return the cached) fit assessment for one student/university
    pair. This is the only way a fit assessment gets produced -- there is no
    direct student-facing endpoint; the student's personal agent calls this
    when a fit/match question comes up in chat.
    """
    from django_api.models import FitAssessment, StudentProfile
    from django_api.services import load_profile_data, make_student_id, save_profile_data

    student_key = make_student_id(student_id)

    if not force:
        cached = (
            FitAssessment.objects.filter(student__student_id=student_key, university_id=university_id)
            .order_by("-created_at")
            .first()
        )
        if cached:
            response_data = dict(cached.assessment)
            response_data["cached"] = True
            response_data["generated_at"] = cached.created_at.isoformat()
            return response_data

    profile = load_profile_data(student_id)
    agent = get_university_agent(university_id)

    try:
        assessment = agent.assess_fit(profile)
    except Exception:
        assessment = None

    if _assessment_failed(assessment):
        assessment = _fallback_fit_assessment(profile, university_id, agent)

    profile.setdefault("assessments", {})
    profile["assessments"][university_id] = assessment
    save_profile_data(student_id, profile)

    row = FitAssessment.objects.create(
        student=StudentProfile.objects.get(student_id=student_key),
        university_id=university_id,
        assessment=assessment,
    )

    response_data = dict(assessment)
    response_data["cached"] = False
    response_data["generated_at"] = row.created_at.isoformat()

    return response_data


# ---------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------

def status() -> str:
    """Show the current state of the Korgut Commons."""
    if not _university_agents:
        return "The Korgut Commons is empty — no agents registered yet."

    lines = [
        f"\n{'=' * 60}",
        "  THE KORGUT COMMONS",
        f"  {len(_university_agents)} university agent(s) active",
        f"{'=' * 60}",
    ]

    for university_id, agent in _university_agents.items():
        try:
            lines.append(f"  {agent.status()}")
        except Exception:
            agent_name = getattr(agent, "persona", {}).get("agent_name", university_id)
            lines.append(f"  {agent_name} ({university_id}) — status unavailable")

    lines.append(f"{'=' * 60}\n")

    return "\n".join(lines)
