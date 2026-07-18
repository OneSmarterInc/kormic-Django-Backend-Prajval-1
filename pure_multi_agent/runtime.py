# pure_multi_agent/runtime.py
# Public entry point for the LangGraph student-agent chat flow:
# run_turn(student_id, message) -> (agent_name, reply).
# caching the context across turns let the agent answer from a
# snapshot that could be minutes or hours stale. Only the LangGraph
# `messages` state (conversation history) is intentionally kept
# in-process via the shared checkpointer below, since that's genuinely
# turn-to-turn conversational state with no other durable home.
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from rich.console import Console

from pure_multi_agent import preprocessing, prompts
from pure_multi_agent.student_graph import build_student_agent
from pure_multi_agent.tracing import VERBOSE, GraphTraceLogger

console = Console()

# Shared checkpointer so conversation history (the `messages` state) persists
# across per-turn graph rebuilds, keyed by thread_id=student key.
# In-process-only lifetime -- lost on worker restart, not shared across
# worker processes. (Swapping in a DB/Redis-backed checkpointer is a real
# follow-up if this ever runs behind more than one worker process, but is
# an infrastructure change, not something to fix silently here.)
_checkpointer = MemorySaver()


def _load_context(student_id: str) -> Dict[str, Any]:
    """Load this student's full turn context fresh from the database. Called
    at the start of every turn -- never cached across turns -- so any
    profile/resume/GitHub/LinkedIn update made through any other endpoint,
    or any agent rename, is always visible on the very next message."""
    from agents.agent_identity import ensure_agent_name
    from django_api.models import AriaMemory, StudentProfile
    from django_api.services import load_profile_data, make_student_id
    from verification.services import list_items

    key = make_student_id(student_id)

    profile_row, _ = StudentProfile.objects.get_or_create(student_id=key)
    agent_name = ensure_agent_name(profile_row)

    student_profile = load_profile_data(student_id)

    memory_row, _ = AriaMemory.objects.get_or_create(student_id=key)
    memory = {
        "important_points": list(memory_row.important_points or []),
        "universities_discussed": list(memory_row.universities_discussed or []),
        "github_profiles_analyzed": list(memory_row.github_profiles_analyzed or []),
    }

    response_mode = student_profile.get("response_mode", "detailed")
    if response_mode not in prompts.VALID_RESPONSE_MODES:
        response_mode = "detailed"

    # The durable source of truth for "is there an open verification item
    # this student hasn't responded to yet" is the VerificationItem table
    # itself, not anything held in memory -- re-derive it every turn instead
    # of threading a flag through a long-lived context object.
    open_items = list_items(key, "open").get("items", [])
    pending_item = open_items[0] if open_items else None

    return {
        "canonical_student_id": key,
        "student_name": student_profile.get("name") or "there",
        "agent_name": agent_name,
        "student_profile": student_profile,
        "memory": memory,
        "response_mode": response_mode,
        "pending_verification_item_id": pending_item["id"] if pending_item else None,
        "pending_verification_item": pending_item,
    }


def _persist_context(student_id: str, ctx: Dict[str, Any]) -> None:
    from django_api.models import AriaMemory
    from django_api.services import make_student_id, save_profile_data

    key = make_student_id(student_id)

    ctx["student_profile"]["response_mode"] = ctx["response_mode"]
    save_profile_data(student_id, ctx["student_profile"])

    AriaMemory.objects.update_or_create(
        student_id=key,
        defaults={
            "important_points": ctx["memory"].get("important_points", [])[-50:],
            "universities_discussed": ctx["memory"].get("universities_discussed", []),
            "github_profiles_analyzed": ctx["memory"].get("github_profiles_analyzed", []),
        },
    )


def _extract_reply_text(result: Dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return "I hit an error while generating a response. Please try again."

    content = messages[-1].content
    if isinstance(content, str):
        return content

    # Anthropic content blocks can come back as a list of dicts/blocks.
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        if parts:
            return "".join(parts)

    return str(content)


def run_turn(student_id: str, message: str) -> tuple[str, str]:
    ctx = _load_context(student_id)

    if VERBOSE:
        console.print(
            f"\n[bold magenta]=== pure_multi_agent turn: student={ctx['canonical_student_id']} "
            f"agent={ctx['agent_name']} ===[/bold magenta]"
        )
        console.print(f"[dim]student says:[/dim] {message}")

    system_prompt = prompts.build_runtime_system_prompt(
        agent_name=ctx["agent_name"],
        student_profile=ctx["student_profile"],
        memory=ctx["memory"],
        response_mode=ctx["response_mode"],
        pending_item=ctx.get("pending_verification_item"),
    )

    agent = build_student_agent(ctx, system_prompt, _checkpointer)
    tracer = GraphTraceLogger(label=ctx["canonical_student_id"])

    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=message)]},
            config={
                "configurable": {"thread_id": ctx["canonical_student_id"]},
                "recursion_limit": 25,
                "callbacks": [tracer],
            },
        )
        reply = _extract_reply_text(result)
    except Exception as exc:
        console.print(f"[yellow]Agent turn failed: {exc}[/yellow]")
        reply = (
            "I hit an error while generating the response. Please check your "
            "ANTHROPIC_API_KEY, network connection, and model access, then try again."
        )

    preprocessing.update_memory(ctx, message, reply)
    _persist_context(student_id, ctx)

    if VERBOSE:
        console.print(f"[bold magenta]=== turn complete ({tracer._step} model call(s)) ===[/bold magenta]\n")

    return ctx["agent_name"], reply
