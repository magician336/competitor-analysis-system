"""Fault-isolated LangChain orchestration for the three core analysis Agents."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableLambda, RunnableParallel

from schemas.benchmark import BenchmarkRun, BenchmarkTask
from schemas.document import EventType
from schemas.intelligence_card import AgentAnalysisRequest, AgentRunResult
from schemas.orchestration import (
    BranchError,
    BranchExecutionStatus,
    BranchOutcome,
    MultiAgentAnalysisRequest,
    MultiAgentAnalysisResult,
    SpecialistBranch,
    WorkflowExecutionStatus,
)

from .benchmark_agent import BenchmarkAgent
from .briefing_agent import BriefingAgent
from .compare_agent import CompareAgent
from .price_agent import PriceAgent
from .product_agent import ProductAgent
from .risk_agent import RiskAgent


_BRANCH_EVENT = {
    SpecialistBranch.PRICE: EventType.PRICING_CHANGE,
    SpecialistBranch.PRODUCT: EventType.PRODUCT_RELEASE,
    SpecialistBranch.RISK: EventType.RISK_EXPERIENCE,
}


class MultiAgentOrchestrator:
    """Parallel fan-out, guarded join and bounded idempotent result cache."""

    def __init__(
        self,
        rag_service: Any | None = None,
        *,
        price_agent: Any | None = None,
        product_agent: Any | None = None,
        risk_agent: Any | None = None,
        compare_agent: Any | None = None,
        briefing_agent: Any | None = None,
        benchmark_agent: Any | None = None,
        cache_max_entries: int = 128,
        auto_configure_llm: bool = True,
    ) -> None:
        if cache_max_entries < 1:
            raise ValueError("cache_max_entries must be at least 1")

        injected = {
            SpecialistBranch.PRICE: price_agent,
            SpecialistBranch.PRODUCT: product_agent,
            SpecialistBranch.RISK: risk_agent,
        }
        missing = [branch for branch, agent in injected.items() if agent is None]
        if missing and rag_service is None:
            names = ", ".join(branch.value for branch in missing)
            raise ValueError(
                f"rag_service is required when specialist Agents are not injected: {names}"
            )
        if injected[SpecialistBranch.PRICE] is None:
            injected[SpecialistBranch.PRICE] = PriceAgent(
                rag_service,
                auto_configure_llm=auto_configure_llm,
            )
        if injected[SpecialistBranch.PRODUCT] is None:
            injected[SpecialistBranch.PRODUCT] = ProductAgent(
                rag_service,
                auto_configure_llm=auto_configure_llm,
            )
        if injected[SpecialistBranch.RISK] is None:
            injected[SpecialistBranch.RISK] = RiskAgent(
                rag_service,
                auto_configure_llm=auto_configure_llm,
            )

        self._agents = injected
        self.compare_agent = compare_agent or CompareAgent()
        self.briefing_agent = briefing_agent or BriefingAgent()
        self.benchmark_agent = benchmark_agent or BenchmarkAgent()
        self._branch_runnables = {
            branch.value: self._make_branch_runnable(branch)
            for branch in SpecialistBranch
        }
        # Keep the concrete RunnableParallel visible for runtime inspection and
        # LangChain tracing. Selected requests build a smaller RunnableParallel.
        self.pipeline = RunnableParallel(dict(self._branch_runnables))

        self._cache_max_entries = cache_max_entries
        self._cache: OrderedDict[str, MultiAgentAnalysisResult] = OrderedDict()
        self._inflight: dict[str, threading.Event] = {}
        self._cache_lock = threading.RLock()

    @property
    def uses_langchain(self) -> bool:
        return isinstance(self.pipeline, RunnableParallel)

    @property
    def cache_size(self) -> int:
        with self._cache_lock:
            return len(self._cache)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def invoke(
        self,
        request: MultiAgentAnalysisRequest | dict[str, Any],
    ) -> MultiAgentAnalysisResult:
        return self.run(request)

    def run(
        self,
        request: MultiAgentAnalysisRequest | dict[str, Any],
    ) -> MultiAgentAnalysisResult:
        parsed = (
            request
            if isinstance(request, MultiAgentAnalysisRequest)
            else MultiAgentAnalysisRequest.model_validate(request)
        )
        if not parsed.use_cache:
            return self._execute(parsed)

        workflow_id = parsed.stable_workflow_id
        while True:
            with self._cache_lock:
                cached = self._cache.get(workflow_id)
                if cached is not None:
                    self._cache.move_to_end(workflow_id)
                    return cached.model_copy(
                        update={"cache_hit": True},
                        deep=True,
                    )
                pending = self._inflight.get(workflow_id)
                if pending is None:
                    pending = threading.Event()
                    self._inflight[workflow_id] = pending
                    break
            # Another caller owns this identical workflow. Wait for its guarded
            # result, then re-check the cache; no duplicate Agent calls occur.
            pending.wait()

        result: MultiAgentAnalysisResult | None = None
        try:
            result = self._execute(parsed)
            with self._cache_lock:
                self._cache[workflow_id] = result.model_copy(deep=True)
                self._cache.move_to_end(workflow_id)
                while len(self._cache) > self._cache_max_entries:
                    self._cache.popitem(last=False)
            return result
        finally:
            with self._cache_lock:
                event = self._inflight.pop(workflow_id, None)
                if event is not None:
                    event.set()

    def _make_branch_runnable(self, branch: SpecialistBranch) -> RunnableLambda:
        def invoke_branch(payload: dict[str, Any]) -> BranchOutcome:
            return self._run_branch(branch, payload["request"])

        return RunnableLambda(
            invoke_branch,
            name=f"safe_{branch.value}_agent",
        )

    def _selected_pipeline(
        self,
        branches: list[SpecialistBranch],
    ) -> RunnableParallel:
        if branches == list(SpecialistBranch):
            return self.pipeline
        return RunnableParallel(
            {
                branch.value: self._branch_runnables[branch.value]
                for branch in branches
            }
        )

    def _run_branch(
        self,
        branch: SpecialistBranch,
        request: MultiAgentAnalysisRequest,
    ) -> BranchOutcome:
        started = time.perf_counter()
        try:
            branch_request = self._branch_request(request, branch)
            raw_result = self._agents[branch].run(branch_request)
            result = (
                raw_result
                if isinstance(raw_result, AgentRunResult)
                else AgentRunResult.model_validate(raw_result)
            )
            return BranchOutcome(
                branch=branch,
                status=BranchExecutionStatus.SUCCESS,
                duration_ms=(time.perf_counter() - started) * 1_000,
                result=result,
            )
        except Exception as exc:
            return BranchOutcome(
                branch=branch,
                status=BranchExecutionStatus.FAILED,
                duration_ms=(time.perf_counter() - started) * 1_000,
                error=self._structured_error(exc),
            )

    @staticmethod
    def _branch_request(
        request: MultiAgentAnalysisRequest,
        branch: SpecialistBranch,
    ) -> AgentAnalysisRequest:
        payload = {
            field_name: getattr(request, field_name)
            for field_name in AgentAnalysisRequest.model_fields
        }
        payload["event_type"] = _BRANCH_EVENT[branch]
        return AgentAnalysisRequest.model_validate(payload)

    @staticmethod
    def _structured_error(exc: Exception, *, stage: str = "analysis") -> BranchError:
        message = " ".join(str(exc).split()).strip() or "branch execution failed"
        return BranchError(
            error_type=type(exc).__name__,
            message=message[:1_000],
            stage=stage,
            retryable=isinstance(exc, (ConnectionError, TimeoutError)),
        )

    def _execute(self, request: MultiAgentAnalysisRequest) -> MultiAgentAnalysisResult:
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        downstream_failed = False
        warnings: list[str] = []
        selected_pipeline = self._selected_pipeline(request.branches)
        try:
            raw_outcomes = selected_pipeline.invoke(
                {"request": request},
                config={
                    "tags": ["week3", "multi-agent", "parallel"],
                    "metadata": {
                        "workflow_id": request.stable_workflow_id,
                        "correlation_id": request.correlation_id,
                        "competitor": request.competitor,
                    },
                },
            )
        except Exception as exc:  # Runnable infrastructure failure, not a branch failure.
            infrastructure_error = self._structured_error(
                exc,
                stage="parallel_dispatch",
            )
            raw_outcomes = {
                branch.value: BranchOutcome(
                    branch=branch,
                    status=BranchExecutionStatus.FAILED,
                    duration_ms=0.0,
                    error=infrastructure_error.model_copy(deep=True),
                )
                for branch in request.branches
            }

        outcomes: dict[SpecialistBranch, BranchOutcome] = {}
        cards = []
        successful_branches = 0
        for branch in request.branches:
            raw = raw_outcomes[branch.value]
            outcome = (
                raw if isinstance(raw, BranchOutcome) else BranchOutcome.model_validate(raw)
            )
            outcomes[branch] = outcome
            if outcome.status == BranchExecutionStatus.SUCCESS:
                successful_branches += 1
                assert outcome.result is not None
                cards.extend(outcome.result.cards)
                warnings.extend(
                    f"{branch.value}: {warning}"
                    for warning in outcome.result.warnings
                )
                if not outcome.result.cards:
                    warnings.append(
                        f"{branch.value} branch completed without intelligence cards"
                    )
            else:
                assert outcome.error is not None
                warnings.append(
                    f"{branch.value} branch failed: "
                    f"{outcome.error.error_type}: {outcome.error.message}"
                )

        unique_cards = list({card.card_id: card for card in cards}.values())
        benchmark_tasks, benchmark_runs, benchmark_warning = self._benchmark_inputs(
            request
        )
        if benchmark_warning:
            warnings.append(benchmark_warning)
            downstream_failed = True

        snapshot = None
        if request.include_snapshot and successful_branches:
            try:
                snapshot = self.compare_agent.build_snapshot(
                    request.competitor,
                    unique_cards,
                    product_version=request.snapshot_product_version,
                    benchmark_tasks=benchmark_tasks,
                    benchmark_runs=benchmark_runs,
                    window_start=request.start_time,
                    window_end=request.end_time,
                )
            except Exception as exc:
                downstream_failed = True
                error = self._structured_error(exc, stage="compare")
                warnings.append(
                    f"compare stage failed: {error.error_type}: {error.message}"
                )
        elif request.include_snapshot:
            warnings.append("snapshot skipped because no specialist branch succeeded")

        briefing = None
        if request.include_briefing and successful_branches:
            try:
                briefing = self.briefing_agent.generate(
                    request.competitor,
                    unique_cards,
                    snapshot,
                )
                if not isinstance(briefing, str) or not briefing.strip():
                    raise ValueError("BriefingAgent returned blank content")
            except Exception as exc:
                downstream_failed = True
                error = self._structured_error(exc, stage="briefing")
                warnings.append(
                    f"briefing stage failed: {error.error_type}: {error.message}"
                )
                briefing = None
        elif request.include_briefing:
            warnings.append("briefing skipped because no specialist branch succeeded")

        failed_branches = len(request.branches) - successful_branches
        if successful_branches == 0:
            status = WorkflowExecutionStatus.FAILED
        elif failed_branches or downstream_failed:
            status = WorkflowExecutionStatus.PARTIAL_FAILURE
        else:
            status = WorkflowExecutionStatus.SUCCESS

        completed_at = datetime.now(timezone.utc)
        return MultiAgentAnalysisResult(
            workflow_id=request.stable_workflow_id,
            request_fingerprint=request.fingerprint,
            request=request,
            status=status,
            cache_hit=False,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=(time.perf_counter() - started) * 1_000,
            branch_outcomes=outcomes,
            cards=unique_cards,
            snapshot=snapshot,
            briefing=briefing,
            warnings=warnings,
        )

    def _benchmark_inputs(
        self,
        request: MultiAgentAnalysisRequest,
    ) -> tuple[list[BenchmarkTask], list[BenchmarkRun], str | None]:
        tasks = {task.task_id: task for task in request.benchmark_tasks}
        runs = {run.run_id: run for run in request.benchmark_runs}
        should_load = request.include_benchmark_data or (runs and not tasks)
        if not should_load:
            return list(tasks.values()), list(runs.values()), None
        try:
            for task in self.benchmark_agent.load_tasks():
                tasks.setdefault(task.task_id, task)
            if request.include_benchmark_data:
                for run in self.benchmark_agent.load_runs():
                    runs.setdefault(run.run_id, run)
        except Exception as exc:
            error = self._structured_error(exc, stage="benchmark_load")
            return (
                list(tasks.values()),
                list(runs.values()),
                f"benchmark stage failed: {error.error_type}: {error.message}",
            )
        return list(tasks.values()), list(runs.values()), None


__all__ = ["MultiAgentOrchestrator"]
