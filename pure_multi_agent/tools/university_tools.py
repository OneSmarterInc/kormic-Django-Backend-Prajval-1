# pure_multi_agent/tools/university_tools.py
# Dynamic university discovery + consultation. The model discovers the
# known universities itself (list_universities) and decides which to call --
# the fixed keyword matcher that used to make this decision on the model's
# behalf (agents.commons.match_university_ids). Single-university calls and the parallel "ask everyone"
# fan-out both go through pure_multi_agent.university_graph, which reuses
# agents.university_agent.UniversityAgent unchanged.

from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from rich.console import Console

from agents import commons
from personas.university_personas import UNIVERSITY_PERSONAS
from pure_multi_agent import university_graph

console = Console()


def _format_result(result: Dict[str, Any], agent_label: str) -> str:
    if result.get("source") == "human_verified":
        return f"[human-verified answer from {agent_label}]\n{result.get('answer', '')}"

    if result.get("pending"):
        pending_query = result.get("pending_query", {}) or {}
        query_id = pending_query.get("query_id", "unknown")
        return (
            f"[{agent_label} could not answer confidently -- pending query "
            f"#{query_id} was created for a university contact]\n"
            f"{result.get('answer', '')}"
        )

    trust = result.get("trust", {})
    confidence = trust.get("confidence", {})

    return (
        f"[{agent_label} answer, confidence={confidence.get('level', 'unknown')}, "
        f"needs_verification={confidence.get('needs_verification', True)}]\n"
        f"{result.get('answer', '')}"
    )


def build_tools(ctx: Dict[str, Any]) -> List[Any]:
    @tool
    def list_universities() -> str:
        """List every university agent available in the Korgut Commons (id
        and display name). Use this to discover what universities you can
        consult before asking one by id."""
        lines = [
            f"- {university_id}: {persona.get('name', university_id)}"
            for university_id, persona in UNIVERSITY_PERSONAS.items()
        ]
        return "\n".join(lines) if lines else "No university agents are configured."

    @tool
    def ask_university(university_id: str, question: str) -> str:
        """Ask one specific university agent (by id, from list_universities) a
        question about that program -- deadlines, requirements, tuition,
        courses, or anything needing verified/scraped/human-verified
        knowledge rather than general advising judgment."""
        result = university_graph.ask_one(university_id, question, ctx["student_profile"])
        agent_label = UNIVERSITY_PERSONAS.get(university_id, {}).get("agent_name", university_id)
        return _format_result(result, agent_label)

    @tool
    def get_fit_assessment(university_id: str) -> str:
        """Get (or generate, if not already saved) a structured fit
        assessment -- match tier, match score, strengths, gaps, recommendation
        -- for the student at one specific university by id. Use this for
        personal-fit/chances/match-score questions about a named university."""
        try:
            assessment = commons.generate_fit_assessment(ctx["canonical_student_id"], university_id)
        except Exception as exc:
            console.print(f"[yellow]Fit assessment failed for {university_id}: {exc}[/yellow]")
            return f"Could not generate a fit assessment for {university_id} right now."

        ctx["student_profile"].setdefault("assessments", {})[university_id] = assessment

        return (
            f"Match tier: {assessment.get('match_tier')}\n"
            f"Match score: {assessment.get('match_score')}\n"
            f"Fit summary: {assessment.get('fit_summary')}\n"
            f"Strengths: {assessment.get('strengths_for_program')}\n"
            f"Gaps: {assessment.get('gaps_for_program')}\n"
            f"Recommendation: {assessment.get('recommendation')}\n"
            f"Specific advice: {assessment.get('specific_advice')}"
        )

    @tool
    def compare_all_universities(question: str) -> str:
        """Ask every known university agent the same question in parallel and
        get back all their answers, for questions that genuinely need input
        from every program (e.g. comparing deadlines or requirements across
        schools). Do not use this for personal-fit comparisons -- use
        get_fit_assessment_for_all_universities for that instead."""
        results = university_graph.ask_all(question, ctx["student_profile"])

        if not results:
            return "Could not get answers from any university agents for that question."

        lines = []
        for result in results:
            agent_label = result.get("agent_name", "University Agent")
            university_name = result.get("university", "Unknown University")
            lines.append(f"{agent_label} ({university_name}): {_format_result(result, agent_label)}")

        return "\n\n".join(lines)

    @tool
    def get_fit_assessment_for_all_universities() -> str:
        """Generate (or reuse saved) fit assessments for every known
        university in parallel, for broad questions like 'which university
        fits me best' or 'where should I apply' that don't name one specific
        school."""
        assessments = ctx["student_profile"].setdefault("assessments", {})
        lines = []

        for university_id in commons.list_university_ids():
            assessment = assessments.get(university_id)
            if not assessment:
                try:
                    assessment = commons.generate_fit_assessment(ctx["canonical_student_id"], university_id)
                    assessments[university_id] = assessment
                except Exception as exc:
                    console.print(f"[yellow]Fit assessment failed for {university_id}: {exc}[/yellow]")
                    continue

            lines.append(
                f"- {assessment.get('university', university_id)}: "
                f"{assessment.get('match_tier', 'unknown')} fit "
                f"(score {assessment.get('match_score', 'n/a')}) -- {assessment.get('fit_summary', '')}"
            )

        if not lines:
            return "Could not generate fit assessments for any university right now."

        return "\n".join(lines)

    return [
        list_universities,
        ask_university,
        get_fit_assessment,
        compare_all_universities,
        get_fit_assessment_for_all_universities,
    ]
