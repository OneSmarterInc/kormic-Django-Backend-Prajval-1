# pure_multi_agent/runtime.py
# Public entry point for the LangGraph student-agent chat flow:
# run_turn(student_id, message) -> (agent_name, reply).
#
# Mirrors the lifetime/caching semantics of agents.commons's existing
# _student_agents cache (one long-lived object per student per worker
# process, not shared across workers, not durable across restarts) so this
# migration doesn't change persistence behavior -- only how routing to
# tools/agents is decided.

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

# Per-student business context (student_profile/memory/pending verification
# item/agent_name), analogous to agents.commons._student_agents.
_student_contexts: Dict[str, Dict[str, Any]] = {}

# Shared checkpointer so conversation history (the `messages` state) persists
# across per-turn graph rebuilds, keyed by thread_id=student key -- same
# in-process-only lifetime as _student_contexts above.
_checkpointer = MemorySaver()


def _build_context(student_id: str) -> Dict[str, Any]:
    from agents.agent_identity import ensure_agent_name
    from django_api.models import AriaMemory, StudentProfile
    from django_api.services import load_profile_data, make_student_id

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

    return {
        "canonical_student_id": key,
        "student_name": student_profile.get("name") or "there",
        "agent_name": agent_name,
        "student_profile": student_profile,
        "memory": memory,
        "response_mode": response_mode,
        "pending_verification_item_id": None,
        "pending_verification_item": None,
    }


def _get_or_build_context(student_id: str) -> Dict[str, Any]:
    from django_api.services import make_student_id

    key = make_student_id(student_id)

    if key not in _student_contexts:
        _student_contexts[key] = _build_context(student_id)

    return _student_contexts[key]


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
    ctx = _get_or_build_context(student_id)

    if VERBOSE:
        console.print(
            f"\n[bold magenta]=== pure_multi_agent turn: student={ctx['canonical_student_id']} "
            f"agent={ctx['agent_name']} ===[/bold magenta]"
        )
        console.print(f"[dim]student says:[/dim] {message}")

    shortcut = preprocessing.roadmap_shortcut(ctx, message)
    if shortcut is not None:
        preprocessing.update_memory(ctx, message, shortcut)
        _persist_context(student_id, ctx)
        if VERBOSE:
            console.print(f"[bold magenta]=== turn resolved via pre-check shortcut ===[/bold magenta]\n")
        return ctx["agent_name"], shortcut

    preprocessing.extract_profile_information(ctx, message)

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


def drop_student_context(student_id: str) -> None:
    """Evict a cached per-student context, e.g. right after agent_name
    changes -- mirrors agents.commons.drop_student_agent."""
    from django_api.services import make_student_id

    _student_contexts.pop(make_student_id(student_id), None)
