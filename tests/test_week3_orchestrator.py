from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from langchain_core.runnables import RunnableParallel
from pydantic import ValidationError

from agents.orchestrator import MultiAgentOrchestrator
from schemas.benchmark import BenchmarkRun, BenchmarkTask
from schemas.capability_snapshot import CapabilitySnapshot
from schemas.document import (
    DimensionTag,
    EventType,
    EvidenceLevel,
    SourceType,
)
from schemas.intelligence_card import (
    AgentAnalysisRequest,
    AgentKind,
    AgentRunResult,
    CapabilityImpact,
    EvidenceReference,
    ImpactDirection,
    IntelligenceCard,
)
from schemas.orchestration import (
    BranchExecutionStatus,
    MultiAgentAnalysisRequest,
    SpecialistBranch,
    WorkflowExecutionStatus,
)


_BRANCH_KIND = {
    SpecialistBranch.PRICE: AgentKind.PRICE,
    SpecialistBranch.PRODUCT: AgentKind.PRODUCT,
    SpecialistBranch.RISK: AgentKind.RISK,
}
_BRANCH_EVENT = {
    SpecialistBranch.PRICE: EventType.PRICING_CHANGE,
    SpecialistBranch.PRODUCT: EventType.PRODUCT_RELEASE,
    SpecialistBranch.RISK: EventType.RISK_EXPERIENCE,
}
_BRANCH_DIMENSION = {
    SpecialistBranch.PRICE: DimensionTag.PERFORMANCE_COST,
    SpecialistBranch.PRODUCT: DimensionTag.AGENT_CONTEXT,
    SpecialistBranch.RISK: DimensionTag.SECURITY_COMPLIANCE,
}


def _card(branch: SpecialistBranch, competitor: str) -> IntelligenceCard:
    dimension = _BRANCH_DIMENSION[branch]
    chunk_id = f"chunk_{branch.value}"
    return IntelligenceCard(
        agent_kind=_BRANCH_KIND[branch],
        competitor=competitor,
        event_type=_BRANCH_EVENT[branch],
        dimension_tags=[dimension],
        event_title=f"{branch.value} event",
        summary=f"{branch.value} evidence-backed summary",
        impact_analysis="Evidence indicates a material competitor change.",
        relevance_to_our_product="high",
        opportunity="Use the evidence to improve CodeMate Campus.",
        threat="The competitor change may narrow differentiation.",
        recommended_action="Validate the change and update the roadmap.",
        confidence_score=0.85,
        priority_score=70,
        evidence=[
            EvidenceReference(
                chunk_id=chunk_id,
                document_id=f"doc_{branch.value}",
                version_id=f"version_{branch.value}",
                title=f"{branch.value} source",
                url=f"https://example.test/{branch.value}",
                competitor=competitor,
                source_type=SourceType.OFFICIAL_PAGE,
                evidence_level=EvidenceLevel.A,
                event_type=_BRANCH_EVENT[branch],
                dimension_tags=[dimension],
                quote="verified source excerpt",
            )
        ],
        impact_details=[
            CapabilityImpact(
                dimension=dimension,
                direction=ImpactDirection.POSITIVE,
                magnitude=7,
                confidence_score=0.85,
                rationale="The retrieved evidence supports this impact.",
                evidence_chunk_ids=[chunk_id],
            )
        ],
    )


class FakeSpecialistAgent:
    def __init__(
        self,
        branch: SpecialistBranch,
        *,
        failure: Exception | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.branch = branch
        self.failure = failure
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.requests: list[AgentAnalysisRequest] = []
        self._lock = threading.Lock()

    def run(self, request: AgentAnalysisRequest) -> AgentRunResult:
        with self._lock:
            self.calls += 1
            self.requests.append(request)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.failure is not None:
            raise self.failure
        return AgentRunResult(
            request=request,
            cards=[_card(self.branch, request.competitor)],
        )


class RecordingCompareAgent:
    def __init__(self) -> None:
        self.calls = 0
        self.cards: list[IntelligenceCard] = []
        self.tasks: list[BenchmarkTask] = []
        self.runs: list[BenchmarkRun] = []

    def build_snapshot(
        self,
        competitor: str,
        cards,
        *,
        benchmark_tasks,
        benchmark_runs,
        product_version=None,
        **kwargs,
    ) -> CapabilitySnapshot:
        self.calls += 1
        self.cards = list(cards)
        self.tasks = list(benchmark_tasks)
        self.runs = list(benchmark_runs)
        return CapabilitySnapshot(
            competitor=competitor,
            product_version=product_version,
        )


class RecordingBriefingAgent:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, competitor, cards, snapshot=None) -> str:
        self.calls += 1
        return f"# {competitor} briefing\n\nCards: {len(cards)}"


class ForbiddenBenchmarkAgent:
    def load_tasks(self):
        raise AssertionError("default benchmark catalog must not be loaded")

    def load_runs(self):
        raise AssertionError("default benchmark results must not be loaded")


def _benchmark_inputs() -> tuple[BenchmarkTask, BenchmarkRun]:
    task = BenchmarkTask(
        task_id="bench_orchestrator",
        name="Orchestration fixture",
        task_type="completion",
        language="Python",
        prompt="Complete the deterministic function.",
        expected_behavior="All fixed assertions pass.",
        primary_dimensions=[DimensionTag.CODE_INTELLIGENCE],
    )
    run = BenchmarkRun(
        run_id="run_orchestrator",
        competitor="Cursor",
        task_id=task.task_id,
        task_revision=task.task_revision,
        task_fingerprint=task.task_fingerprint,
        validator_sha256="1" * 64,
        protocol_sha256="2" * 64,
        starter_sha256="3" * 64,
        candidate_sha256="4" * 64,
        task_success=True,
        compile_success=True,
        test_pass_rate=1.0,
        run_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    return task, run


def _orchestrator(
    *,
    price: FakeSpecialistAgent | None = None,
    product: FakeSpecialistAgent | None = None,
    risk: FakeSpecialistAgent | None = None,
    cache_max_entries: int = 8,
) -> tuple[
    MultiAgentOrchestrator,
    FakeSpecialistAgent,
    FakeSpecialistAgent,
    FakeSpecialistAgent,
    RecordingCompareAgent,
    RecordingBriefingAgent,
]:
    price = price or FakeSpecialistAgent(SpecialistBranch.PRICE)
    product = product or FakeSpecialistAgent(SpecialistBranch.PRODUCT)
    risk = risk or FakeSpecialistAgent(SpecialistBranch.RISK)
    compare = RecordingCompareAgent()
    briefing = RecordingBriefingAgent()
    workflow = MultiAgentOrchestrator(
        price_agent=price,
        product_agent=product,
        risk_agent=risk,
        compare_agent=compare,
        briefing_agent=briefing,
        benchmark_agent=ForbiddenBenchmarkAgent(),
        cache_max_entries=cache_max_entries,
    )
    return workflow, price, product, risk, compare, briefing


def test_uses_real_runnable_parallel_and_all_branches_join_into_outputs() -> None:
    workflow, price, product, risk, compare, briefing = _orchestrator()
    task, run = _benchmark_inputs()
    request = MultiAgentAnalysisRequest(
        competitor="Cursor",
        correlation_id="demo-001",
        snapshot_product_version="2026.07",
        benchmark_tasks=[task],
        benchmark_runs=[run],
    )

    result = workflow.run(request)

    assert isinstance(workflow.pipeline, RunnableParallel)
    assert workflow.uses_langchain is True
    assert result.status == WorkflowExecutionStatus.SUCCESS
    assert result.partial_failure is False
    assert result.cache_hit is False
    assert result.duration_ms >= max(
        outcome.duration_ms for outcome in result.branch_outcomes.values()
    )
    assert set(result.branch_outcomes) == set(SpecialistBranch)
    assert all(
        outcome.status == BranchExecutionStatus.SUCCESS
        for outcome in result.branch_outcomes.values()
    )
    assert len(result.cards) == 3
    assert result.snapshot is not None
    assert result.snapshot.product_version == "2026.07"
    assert result.briefing.startswith("# Cursor briefing")
    assert compare.cards == result.cards
    assert [item.task_id for item in compare.tasks] == [task.task_id]
    assert [item.run_id for item in compare.runs] == [run.run_id]
    assert briefing.calls == 1
    assert [price.calls, product.calls, risk.calls] == [1, 1, 1]
    assert price.requests[0].event_type == EventType.PRICING_CHANGE
    assert product.requests[0].event_type == EventType.PRODUCT_RELEASE
    assert risk.requests[0].event_type == EventType.RISK_EXPERIENCE


def test_one_branch_failure_is_isolated_and_downstream_still_runs() -> None:
    risk = FakeSpecialistAgent(
        SpecialistBranch.RISK,
        failure=RuntimeError("simulated risk failure"),
    )
    workflow, _, _, _, compare, briefing = _orchestrator(risk=risk)

    result = workflow.run(MultiAgentAnalysisRequest(competitor="Cursor"))

    assert result.status == WorkflowExecutionStatus.PARTIAL_FAILURE
    assert result.partial_failure is True
    assert len(result.cards) == 2
    assert result.snapshot is not None
    assert result.briefing is not None
    assert compare.calls == 1
    assert briefing.calls == 1
    failed = result.branch_outcomes[SpecialistBranch.RISK]
    assert failed.status == BranchExecutionStatus.FAILED
    assert failed.result is None
    assert failed.error is not None
    assert failed.error.error_type == "RuntimeError"
    assert failed.error.stage == "analysis"
    assert any("risk branch failed" in warning for warning in result.warnings)


def test_selected_branch_does_not_call_unselected_agents() -> None:
    workflow, price, product, risk, compare, briefing = _orchestrator()
    request = MultiAgentAnalysisRequest(
        competitor="Cursor",
        branches=[SpecialistBranch.PRICE],
        include_snapshot=False,
        include_briefing=False,
    )

    result = workflow.run(request)

    assert result.status == WorkflowExecutionStatus.SUCCESS
    assert set(result.branch_outcomes) == {SpecialistBranch.PRICE}
    assert len(result.cards) == 1
    assert [price.calls, product.calls, risk.calls] == [1, 0, 0]
    assert compare.calls == 0
    assert briefing.calls == 0


def test_identical_request_is_cached_and_concurrent_calls_are_coalesced() -> None:
    price = FakeSpecialistAgent(SpecialistBranch.PRICE, delay_seconds=0.05)
    product = FakeSpecialistAgent(SpecialistBranch.PRODUCT, delay_seconds=0.05)
    risk = FakeSpecialistAgent(SpecialistBranch.RISK, delay_seconds=0.05)
    workflow, _, _, _, _, _ = _orchestrator(
        price=price,
        product=product,
        risk=risk,
    )
    request = MultiAgentAnalysisRequest(
        competitor="Cursor",
        correlation_id="same-request",
        include_snapshot=False,
        include_briefing=False,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: workflow.run(request), range(2)))
    replay = workflow.run(request)

    assert results[0].workflow_id == results[1].workflow_id == replay.workflow_id
    assert results[0].request_fingerprint == results[1].request_fingerprint
    assert sum(result.cache_hit for result in results) == 1
    assert replay.cache_hit is True
    assert [price.calls, product.calls, risk.calls] == [1, 1, 1]
    assert workflow.cache_size == 1


def test_cache_is_bounded_and_request_content_changes_workflow_id() -> None:
    workflow, *_ = _orchestrator(cache_max_entries=2)
    results = [
        workflow.run(
            MultiAgentAnalysisRequest(
                competitor="Cursor",
                question=f"question {index}",
                include_snapshot=False,
                include_briefing=False,
            )
        )
        for index in range(3)
    ]

    assert len({result.workflow_id for result in results}) == 3
    assert workflow.cache_size == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"competitor": "Cursor", "branches": []},
        {"competitor": "Cursor", "branches": ["price", "price"]},
        {"competitor": "Cursor", "correlation_id": "   "},
        {"competitor": "Cursor", "unexpected": True},
        {
            "competitor": "Cursor",
            "event_type": "pricing_change",
            "branches": ["price", "product"],
        },
    ],
)
def test_request_contract_is_strict(payload) -> None:
    with pytest.raises(ValidationError):
        MultiAgentAnalysisRequest.model_validate(payload)
