# agents/commons.py
# The Korgut Commons — where agents live and communicate.
# Registry and communication layer for all university agents.

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import anthropic
from rich.console import Console

console = Console()

# The Commons registry — all active university agents register here.
_university_agents: Dict[str, Any] = {}


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
    Return a registered university agent without asking it a question.

    Used by Aria to lazily generate fit assessments only when a
    student asks a fit/match/profile question.
    """
    return _university_agents.get(university_id)


def list_agents() -> List[str]:
    """Return IDs of all registered university agents."""
    return list(_university_agents.keys())


def query(
    university_id: str,
    question: str,
    student_context: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """
    Query a specific university agent.

    Called by Aria when she needs verified information about a program.
    """
    agent = _university_agents.get(university_id)

    if not agent:
        console.print(f"[yellow]No agent registered for {university_id}.[/yellow]")
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
) -> List[Dict[str, Any]]:
    """
    Broadcast a question to all registered university agents.

    Useful for cross-program comparisons.
    """
    responses: List[Dict[str, Any]] = []

    for university_id, agent in _university_agents.items():
        try:
            response = agent.answer(question, student_context)
            if response:
                responses.append(response)
        except Exception as exc:
            console.print(f"[yellow]Query failed for {university_id}: {exc}[/yellow]")
            responses.append(
                {
                    "agent_name": getattr(agent, "persona", {}).get("agent_name", university_id),
                    "university": getattr(agent, "persona", {}).get("university", university_id),
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
    When Aria receives answers from multiple university agents,
    synthesise them into one clear, personalised answer for the student.
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

    synthesis_prompt = f"""You are Aria. Multiple university agents in the
Korgut Commons have answered a question. Synthesise their responses into a
single clear, personalised answer for {student_name}.

Be specific. Cite university names where relevant. If the agents gave
different answers, note the differences clearly. Keep your answer
conversational and direct.

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

        # Fallback: return combined agent answers instead of crashing Aria.
        return (
            "I could not synthesise the responses automatically, but here are the "
            "university agent answers:\n\n"
            + compiled
        )


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
