"""Service layer for week-three evidence-backed Agents."""

from __future__ import annotations

import threading
from typing import Any

from agents import (
    BenchmarkAgent,
    BriefingAgent,
    CompareAgent,
    MultiAgentOrchestrator,
    PriceAgent,
    ProductAgent,
    RiskAgent,
)
from schemas.capability_snapshot import CapabilitySnapshot
from schemas.intelligence_card import (
    AgentAnalysisRequest,
    AgentRunResult,
    IntelligenceCard,
)
from schemas.orchestration import MultiAgentAnalysisRequest, MultiAgentAnalysisResult

from ..repositories import AnalysisRepository, get_analysis_repository
from .rag_service import get_rag_service


class AgentService:
    """Thread-safe facade over the reusable week-three Agent graph.

    Every dependency can be injected for offline tests.  In normal operation the
    orchestrator receives the exact same specialist, comparison, briefing and
    benchmark instances exposed by this service, so caches and configuration do
    not silently diverge between single-Agent and Multi-Agent execution.
    """

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
        orchestrator: Any | None = None,
        repository: AnalysisRepository | None = None,
    ) -> None:
        self.price_agent = (
            price_agent if price_agent is not None else PriceAgent(rag_service)
        )
        self.product_agent = (
            product_agent if product_agent is not None else ProductAgent(rag_service)
        )
        self.risk_agent = (
            risk_agent if risk_agent is not None else RiskAgent(rag_service)
        )
        self.compare_agent = (
            compare_agent if compare_agent is not None else CompareAgent()
        )
        self.briefing_agent = (
            briefing_agent if briefing_agent is not None else BriefingAgent()
        )
        self.benchmark_agent = (
            benchmark_agent if benchmark_agent is not None else BenchmarkAgent()
        )
        self.orchestrator = (
            orchestrator
            if orchestrator is not None
            else MultiAgentOrchestrator(
                price_agent=self.price_agent,
                product_agent=self.product_agent,
                risk_agent=self.risk_agent,
                compare_agent=self.compare_agent,
                briefing_agent=self.briefing_agent,
                benchmark_agent=self.benchmark_agent,
            )
        )
        self.repository = repository or get_analysis_repository()
        # Snapshot builds are serialized so two concurrent requests cannot both
        # select the same historical predecessor and fork the delta chain.
        self._snapshot_build_lock = threading.RLock()

    def analyze_price(self, request: AgentAnalysisRequest) -> AgentRunResult:
        return self._persist_agent_result(self.price_agent.run(request))

    def analyze_product(self, request: AgentAnalysisRequest) -> AgentRunResult:
        return self._persist_agent_result(self.product_agent.run(request))

    def analyze_risk(self, request: AgentAnalysisRequest) -> AgentRunResult:
        return self._persist_agent_result(self.risk_agent.run(request))

    def analyze_all(
        self,
        request: MultiAgentAnalysisRequest,
    ) -> MultiAgentAnalysisResult:
        """Run the fault-isolated Multi-Agent graph and retain its artifacts."""

        raw_result = self.orchestrator.run(request)
        result = (
            raw_result
            if isinstance(raw_result, MultiAgentAnalysisResult)
            else MultiAgentAnalysisResult.model_validate(raw_result)
        )
        with self._snapshot_build_lock:
            self.repository.save_workflow_result(result)
        return result

    def list_cards(self, competitor: str | None = None) -> list[IntelligenceCard]:
        return self.repository.list_cards(competitor)

    def get_card(self, card_id: str) -> IntelligenceCard | None:
        return self.repository.get_card(card_id)

    def build_snapshot(
        self,
        competitor: str,
        *,
        product_version: str | None = None,
    ) -> CapabilitySnapshot:
        snapshot, _, _ = self._build_snapshot_with_history(
            competitor,
            product_version=product_version,
        )
        return snapshot

    def list_snapshots(self, competitor: str | None = None) -> list[CapabilitySnapshot]:
        return self.repository.list_snapshots(competitor)

    def generate_briefing(
        self,
        competitor: str,
        *,
        product_version: str | None = None,
    ) -> dict[str, Any]:
        snapshot, previous_snapshot, cards = self._build_snapshot_with_history(
            competitor,
            product_version=product_version,
            persist=False,
        )
        markdown = self.briefing_agent.generate(
            competitor,
            cards,
            snapshot,
            previous_snapshot=previous_snapshot,
        )
        stored_snapshot, _ = self.repository.save_snapshot_and_briefing(
            snapshot=snapshot,
            competitor=competitor,
            markdown=markdown,
            payload={
                "competitor": competitor,
                "snapshot_id": snapshot.snapshot_id,
                "card_ids": [card.card_id for card in cards],
            },
        )
        return {
            "competitor": competitor,
            "markdown": markdown,
            "cards": cards,
            "snapshot": stored_snapshot,
        }

    def _persist_agent_result(self, raw_result: AgentRunResult) -> AgentRunResult:
        result = AgentRunResult.model_validate(raw_result)
        self.repository.save_agent_result(result)
        return result

    def _build_snapshot_with_history(
        self,
        competitor: str,
        *,
        product_version: str | None,
        persist: bool = True,
    ) -> tuple[
        CapabilitySnapshot,
        CapabilitySnapshot | None,
        list[IntelligenceCard],
    ]:
        with self._snapshot_build_lock:
            cards = self.repository.list_cards(competitor)
            previous_snapshot = self.repository.latest_snapshot(competitor)

            raw_snapshot = self.compare_agent.build_snapshot(
                competitor,
                cards,
                product_version=product_version,
                benchmark_tasks=[],
                benchmark_runs=[],
                previous_snapshot=previous_snapshot,
            )
            snapshot = (
                raw_snapshot
                if isinstance(raw_snapshot, CapabilitySnapshot)
                else CapabilitySnapshot.model_validate(raw_snapshot)
            )

            if not persist:
                return snapshot, previous_snapshot, cards

            stored = self.repository.save_snapshot(snapshot, preserve_existing=True)
            if stored.snapshot_id != snapshot.snapshot_id:
                raise RuntimeError("stored snapshot identity changed unexpectedly")
            predecessor = (
                self.repository.get_snapshot(stored.previous_snapshot_id)
                if stored.previous_snapshot_id
                else (
                    previous_snapshot
                    if previous_snapshot is not None
                    and previous_snapshot.snapshot_id != stored.snapshot_id
                    else None
                )
            )
            return stored, predecessor, cards


_lock = threading.RLock()
_service: AgentService | None = None


def get_agent_service() -> AgentService:
    global _service
    with _lock:
        if _service is None:
            _service = AgentService(get_rag_service())
        return _service


def set_agent_service(service: AgentService | None) -> None:
    global _service
    with _lock:
        _service = service


__all__ = ["AgentService", "get_agent_service", "set_agent_service"]
