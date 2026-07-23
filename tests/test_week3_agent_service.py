from __future__ import annotations

from datetime import date

from agents import DimensionTaggingAgent, MultiAgentOrchestrator
from backend.services.agent_service import AgentService
from schemas import (
    BenchmarkContribution,
    BranchOutcome,
    CapabilityScoreStatus,
    DimensionTaggingResult,
    EvidenceContribution,
    MultiAgentAnalysisRequest,
    MultiAgentAnalysisResult,
    PriorityBreakdown,
    RiskLevel,
    SpecialistBranch,
    TagMergeStrategy,
    WorkflowExecutionStatus,
)
from schemas.capability_snapshot import CapabilitySnapshot
from schemas.document import EventType
from schemas.intelligence_card import (
    AgentAnalysisRequest,
    AgentKind,
    AgentRunResult,
    IntelligenceCard,
)


class NoopSpecialist:
    def run(self, request):  # pragma: no cover - identity-only dependency
        raise AssertionError("the identity test must not execute specialists")


class RecordingCompare:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def build_snapshot(
        self,
        competitor,
        cards,
        *,
        product_version,
        benchmark_tasks,
        benchmark_runs,
        previous_snapshot,
    ) -> CapabilitySnapshot:
        self.calls.append(
            {
                "competitor": competitor,
                "cards": list(cards),
                "product_version": product_version,
                "benchmark_tasks": list(benchmark_tasks),
                "benchmark_runs": list(benchmark_runs),
                "previous_snapshot": previous_snapshot,
            }
        )
        return CapabilitySnapshot(
            snapshot_id=f"snap_service_{len(self.calls)}",
            competitor=competitor,
            snapshot_date=date(2026, 7, len(self.calls)),
            product_version=product_version,
            previous_snapshot_id=(
                previous_snapshot.snapshot_id if previous_snapshot else None
            ),
        )


class RecordingBriefing:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        competitor,
        cards,
        snapshot,
        previous_snapshot=None,
    ) -> str:
        self.calls.append(
            {
                "competitor": competitor,
                "cards": list(cards),
                "snapshot": snapshot,
                "previous_snapshot": previous_snapshot,
            }
        )
        return "# service briefing"


class FakeBenchmark:
    def __init__(self) -> None:
        self.tasks = [object()]
        self.runs = [
            type("Run", (), {"competitor": "Cursor"})(),
            type("Run", (), {"competitor": "Copilot"})(),
        ]

    def load_tasks(self):
        return list(self.tasks)

    def load_runs(self):
        return list(self.runs)


class StaticOrchestrator:
    def __init__(self, result: MultiAgentAnalysisResult) -> None:
        self.result = result
        self.calls: list[MultiAgentAnalysisRequest] = []

    def run(self, request: MultiAgentAnalysisRequest) -> MultiAgentAnalysisResult:
        self.calls.append(request)
        return self.result


def _card(competitor: str = "Cursor") -> IntelligenceCard:
    return IntelligenceCard(
        agent_kind=AgentKind.PRICE,
        competitor=competitor,
        event_type=EventType.PRICING_CHANGE,
        event_title="Pricing update",
        summary="A scoped service test card.",
        impact_analysis="Requires review because this fixture has no evidence.",
        relevance_to_our_product="Used to verify artifact retention.",
        opportunity="Validate with official evidence.",
        threat="Unknown until evidence is retrieved.",
        recommended_action="Request a human review.",
        confidence_score=0.2,
        priority_score=20,
    )


def _service_dependencies():
    return NoopSpecialist(), NoopSpecialist(), NoopSpecialist()


def test_public_packages_export_week_three_agents_and_contracts() -> None:
    assert DimensionTaggingAgent.__name__ == "DimensionTaggingAgent"
    assert MultiAgentOrchestrator.__name__ == "MultiAgentOrchestrator"
    assert MultiAgentAnalysisRequest.__name__ == "MultiAgentAnalysisRequest"
    assert MultiAgentAnalysisResult.__name__ == "MultiAgentAnalysisResult"
    assert BranchOutcome.__name__ == "BranchOutcome"
    assert BenchmarkContribution.__name__ == "BenchmarkContribution"
    assert EvidenceContribution.__name__ == "EvidenceContribution"
    assert PriorityBreakdown.__name__ == "PriorityBreakdown"
    assert DimensionTaggingResult.__name__ == "DimensionTaggingResult"
    assert CapabilityScoreStatus.INSUFFICIENT_EVIDENCE.value == "insufficient_evidence"
    assert RiskLevel.UNKNOWN.value == "unknown"
    assert TagMergeStrategy.UNION.value == "union"


def test_service_constructs_orchestrator_with_shared_agent_instances(
    analysis_repository,
) -> None:
    price, product, risk = _service_dependencies()
    compare = RecordingCompare()
    briefing = RecordingBriefing()
    benchmark = FakeBenchmark()
    service = AgentService(
        price_agent=price,
        product_agent=product,
        risk_agent=risk,
        compare_agent=compare,
        briefing_agent=briefing,
        benchmark_agent=benchmark,
        repository=analysis_repository,
    )

    assert service.orchestrator._agents[SpecialistBranch.PRICE] is price
    assert service.orchestrator._agents[SpecialistBranch.PRODUCT] is product
    assert service.orchestrator._agents[SpecialistBranch.RISK] is risk
    assert service.orchestrator.compare_agent is compare
    assert service.orchestrator.briefing_agent is briefing
    assert service.orchestrator.benchmark_agent is benchmark


def test_analyze_all_retains_orchestrated_cards_and_snapshot(
    analysis_repository,
) -> None:
    request = MultiAgentAnalysisRequest(
        competitor="Cursor",
        include_briefing=False,
    )
    card = _card()
    snapshot = CapabilitySnapshot(
        snapshot_id="snap_orchestrated",
        competitor="Cursor",
    )
    result = MultiAgentAnalysisResult(
        workflow_id=request.stable_workflow_id,
        request_fingerprint=request.fingerprint,
        request=request,
        status=WorkflowExecutionStatus.SUCCESS,
        duration_ms=1,
        cards=[card],
        snapshot=snapshot,
    )
    orchestrator = StaticOrchestrator(result)
    price, product, risk = _service_dependencies()
    service = AgentService(
        price_agent=price,
        product_agent=product,
        risk_agent=risk,
        compare_agent=RecordingCompare(),
        briefing_agent=RecordingBriefing(),
        benchmark_agent=FakeBenchmark(),
        orchestrator=orchestrator,
        repository=analysis_repository,
    )

    returned = service.analyze_all(request)

    assert returned is result
    assert orchestrator.calls == [request]
    assert service.get_card(card.card_id) == card
    assert service.list_snapshots("cursor") == [snapshot]


def test_snapshot_ignores_benchmarks_and_briefing_receives_true_predecessor(
    analysis_repository,
) -> None:
    price, product, risk = _service_dependencies()
    compare = RecordingCompare()
    briefing = RecordingBriefing()
    benchmark = FakeBenchmark()
    service = AgentService(
        price_agent=price,
        product_agent=product,
        risk_agent=risk,
        compare_agent=compare,
        briefing_agent=briefing,
        benchmark_agent=benchmark,
        orchestrator=object(),
        repository=analysis_repository,
    )
    service.repository.save_agent_result(
        AgentRunResult(
            request=AgentAnalysisRequest(competitor="CURSOR"),
            cards=[_card("CURSOR")],
        )
    )

    first = service.build_snapshot("Cursor", product_version="1.0")
    briefing_result = service.generate_briefing("cursor", product_version="1.1")
    second = briefing_result["snapshot"]

    assert compare.calls[0]["benchmark_tasks"] == []
    assert compare.calls[0]["benchmark_runs"] == []
    assert compare.calls[0]["previous_snapshot"] is None
    assert compare.calls[1]["previous_snapshot"] == first
    assert second.previous_snapshot_id == first.snapshot_id
    assert briefing.calls[0]["snapshot"] == second
    assert briefing.calls[0]["previous_snapshot"] == first
    assert briefing.calls[0]["previous_snapshot"] != second
    assert briefing_result["markdown"] == "# service briefing"
