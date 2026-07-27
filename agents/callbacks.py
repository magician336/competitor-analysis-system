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
        self.llm_call_count = 0
        self.tool_call_count = 0
        self.react_used = False
        self.react_iterations = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self._llm_run_ids: set[UUID] = set()
        self._tool_run_ids: set[UUID] = set()

    def _mark_llm_call(self, run_id: UUID) -> None:
        with self._lock:
            if run_id not in self._llm_run_ids:
                self._llm_run_ids.add(run_id)
                self.llm_call_count += 1

    def mark_react(self, iterations: int) -> None:
        with self._lock:
            self.react_used = iterations > 0
            self.react_iterations = max(self.react_iterations, iterations)

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
        with self._lock:
            if run_id not in self._tool_run_ids:
                self._tool_run_ids.add(run_id)
                self.tool_call_count += 1
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
        self._mark_llm_call(run_id)
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
        self._mark_llm_call(run_id)
        self._append(self._name(serialized, kwargs), "start")

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._record_usage(response)
        self._append(str(kwargs.get("name") or "llm"), "end")

    def _record_usage(self, response: Any) -> None:
        usage: dict[str, Any] = {}
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            candidate = llm_output.get("token_usage") or llm_output.get("usage")
            if isinstance(candidate, dict):
                usage = candidate
        if not usage:
            for generation_group in getattr(response, "generations", []) or []:
                for generation in generation_group or []:
                    message = getattr(generation, "message", None)
                    candidate = getattr(message, "usage_metadata", None)
                    if isinstance(candidate, dict):
                        usage = candidate
                        break
                if usage:
                    break
        input_tokens = int(
            usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        )
        output_tokens = int(
            usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        )
        total_tokens = int(
            usage.get("total_tokens", input_tokens + output_tokens) or 0
        )
        with self._lock:
            self.input_tokens += max(0, input_tokens)
            self.output_tokens += max(0, output_tokens)
            self.total_tokens += max(0, total_tokens)

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
            llm_call_count=self.llm_call_count,
            tool_call_count=self.tool_call_count,
            react_used=self.react_used,
            react_iterations=self.react_iterations,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.total_tokens,
        )


__all__ = ["AgentTraceCallback"]
