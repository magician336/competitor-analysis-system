"""Small LangChain callback collector for auditable Agent execution traces."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from langchain_core.callbacks import BaseCallbackHandler

from schemas.intelligence_card import (
    AgentExecutionTrace,
    AgentKind,
    AgentTraceEvent,
)


class AgentTraceCallback(BaseCallbackHandler):
    """Collect chain/tool/model lifecycle events without recording prompt text."""

    raise_error = False
    run_inline = True

    def __init__(self) -> None:
        self.trace_id = f"trace_{uuid4().hex}"
        self._started = time.perf_counter()
        self._events: list[AgentTraceEvent] = []
        self._lock = threading.RLock()
        self.llm_used = False

    @staticmethod
    def _name(serialized: dict[str, Any] | None, kwargs: dict[str, Any]) -> str:
        explicit = kwargs.get("name")
        if explicit:
            return str(explicit)
        if serialized:
            return str(serialized.get("name") or serialized.get("id") or "runnable")
        return "runnable"

    def _append(self, stage: str, event: str, detail: str | None = None) -> None:
        with self._lock:
            self._events.append(
                AgentTraceEvent(
                    stage=stage,
                    event=event,
                    at=datetime.now(timezone.utc),
                    detail=detail,
                )
            )

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._append(self._name(serialized, kwargs), "start")

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._append(str(kwargs.get("name") or "chain"), "end")

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._append(
            str(kwargs.get("name") or "chain"),
            "error",
            f"{type(error).__name__}: {error}",
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._append(self._name(serialized, kwargs), "start")

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._append(str(kwargs.get("name") or "tool"), "end")

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._append(
            str(kwargs.get("name") or "tool"),
            "error",
            f"{type(error).__name__}: {error}",
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any] | None,
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self.llm_used = True
        self._append(self._name(serialized, kwargs), "start")

    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: list[list[Any]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self.llm_used = True
        self._append(self._name(serialized, kwargs), "start")

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._append(str(kwargs.get("name") or "llm"), "end")

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._append(
            str(kwargs.get("name") or "llm"),
            "error",
            f"{type(error).__name__}: {error}",
        )

    def finish(
        self,
        *,
        agent_kind: AgentKind,
        fallback_used: bool,
        model_name: str | None,
    ) -> AgentExecutionTrace:
        return AgentExecutionTrace(
            trace_id=self.trace_id,
            agent_kind=agent_kind,
            duration_ms=round((time.perf_counter() - self._started) * 1000, 3),
            events=list(self._events),
            llm_used=self.llm_used,
            fallback_used=fallback_used,
            model_name=model_name,
        )


__all__ = ["AgentTraceCallback"]
