# pure_multi_agent/tracing.py
# Terminal tracing for the LangGraph student agent -- prints every model call
# and every tool call/result as they happen, so you can see the agent's
# actual tool/agent-selection decisions live instead of guessing. Purely for
# understanding/debugging; toggle off with PURE_MULTI_AGENT_VERBOSE=false.

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler
from rich.console import Console

console = Console()

VERBOSE = os.getenv("PURE_MULTI_AGENT_VERBOSE", "true").strip().lower() not in {"0", "false", "no"}


def _truncate(text: Any, limit: int = 400) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 15] + "... [truncated]"


def _message_preview(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
    return _truncate(content, 200)


class GraphTraceLogger(BaseCallbackHandler):
    """Logs the student agent's reasoning loop to the terminal:
    - every time the model is invoked (and with how much context)
    - every tool the model decides to call, with its arguments
    - every tool's result
    - the model's final natural-language decision (tool call vs direct reply)
    """

    def __init__(self, label: str = ""):
        self.label = label
        self._step = 0

    def _tag(self) -> str:
        return f"[bold blue]\\[{self.label}][/bold blue]" if self.label else ""

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[Any]],
        *,
        run_id,
        **kwargs: Any,
    ) -> None:
        if not VERBOSE:
            return
        self._step += 1
        history = messages[0] if messages else []
        console.print(
            f"{self._tag()} [dim]step {self._step}: asking the model "
            f"({len(history)} messages of context so far)[/dim]"
        )

    def on_llm_end(self, response, *, run_id, **kwargs: Any) -> None:
        if not VERBOSE:
            return
        try:
            message = response.generations[0][0].message
        except Exception:
            return

        tool_calls = getattr(message, "tool_calls", None) or []

        if tool_calls:
            for call in tool_calls:
                args = json.dumps(call.get("args", {}), ensure_ascii=False)
                console.print(
                    f"{self._tag()} [bold cyan]model decided to call tool[/bold cyan] "
                    f"{call.get('name')}({args})"
                )
        else:
            console.print(
                f"{self._tag()} [bold green]model produced a final reply:[/bold green] "
                f"{_message_preview(message)}"
            )

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id,
        inputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        if not VERBOSE:
            return
        name = serialized.get("name", "tool")
        payload = inputs if inputs is not None else input_str
        console.print(
            f"{self._tag()} [yellow]-> running tool[/yellow] {name}"
            f"({_truncate(payload, 300)})"
        )

    def on_tool_end(self, output: Any, *, run_id, **kwargs: Any) -> None:
        if not VERBOSE:
            return
        text = output if isinstance(output, str) else getattr(output, "content", str(output))
        console.print(f"{self._tag()} [green]<- tool result:[/green] {_truncate(text, 400)}")

    def on_tool_error(self, error: BaseException, *, run_id, **kwargs: Any) -> None:
        console.print(f"{self._tag()} [red]tool error: {error}[/red]")
