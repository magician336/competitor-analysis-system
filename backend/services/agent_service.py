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
        self._cards: dict[str, IntelligenceCard] = {}
        self._snapshots: dict[str, CapabilitySnapshot] = {}
        self._lock = threading.RLock()
        # Snapshot builds are serialized so two concurrent requests cannot both
        # select the same historical predecessor and fork the delta chain.
        self._snapshot_build_lock = threading.RLock()

    def analyze_price(self, request: AgentAnalysisRequest) -> AgentRunResult:
        return self._store_result(self.price_agent.run(request))

    def analyze_product(self, request: AgentAnalysisRequest) -> AgentRunResult:
        return self._store_result(self.product_agent.run(request))

    def analyze_risk(self, request: AgentAnalysisRequest) -> AgentRunResult:
        return self._store_result(self.risk_agent.run(request))

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
        with self._snapshot_build_lock, self._lock:
            self._store_cards_locked(result.cards)
            if result.snapshot is not None:
                # A cache replay or idempotent workflow must not erase a locally
                # enriched snapshot that already carries historical deltas.
                self._snapshots.setdefault(
                    result.snapshot.snapshot_id,
                    result.snapshot,
                )
        return result

    def list_cards(self, competitor: str | None = None) -> list[IntelligenceCard]:
        with self._lock:
            cards = list(self._cards.values())
        if competitor:
            target = competitor.strip().casefold()
            cards = [
                card for card in cards if card.competitor.strip().casefold() == target
            ]
        return sorted(cards, key=lambda item: item.created_at, reverse=True)

    def get_card(self, card_id: str) -> IntelligenceCard | None:
        with self._lock:
            return self._cards.get(card_id)

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
        with self._lock:
            indexed = list(enumerate(self._snapshots.values()))
        if competitor:
            target = competitor.strip().casefold()
            indexed = [
                (position, snapshot)
                for position, snapshot in indexed
                if snapshot.competitor.strip().casefold() == target
            ]
        return [
            snapshot
            for _, snapshot in sorted(
                indexed,
                key=lambda item: (item[1].snapshot_date, item[0]),
                reverse=True,
            )
        ]

    def generate_briefing(
        self,
        competitor: str,
        *,
        product_version: str | None = None,
    ) -> dict[str, Any]:
        snapshot, previous_snapshot, cards = self._build_snapshot_with_history(
            competitor,
            product_version=product_version,
        )
        return {
            "competitor": competitor,
            "markdown": self.briefing_agent.generate(
                competitor,
                cards,
                snapshot,
                previous_snapshot=previous_snapshot,
            ),
            "cards": cards,
            "snapshot": snapshot,
        }

    def _store_result(self, result: AgentRunResult) -> AgentRunResult:
        with self._lock:
            self._store_cards_locked(result.cards)
        return result

    def _store_cards_locked(self, cards: list[IntelligenceCard]) -> None:
        for card in cards:
            self._cards[card.card_id] = card

    def _build_snapshot_with_history(
        self,
        competitor: str,
        *,
        product_version: str | None,
    ) -> tuple[
        CapabilitySnapshot,
        CapabilitySnapshot | None,
        list[IntelligenceCard],
    ]:
        benchmark_tasks = list(self.benchmark_agent.load_tasks())
        target = competitor.strip().casefold()
        benchmark_runs = [
            run
            for run in self.benchmark_agent.load_runs()
            if run.competitor.strip().casefold() == target
        ]

        with self._snapshot_build_lock:
            with self._lock:
                cards = [
                    card
                    for card in self._cards.values()
                    if card.competitor.strip().casefold() == target
                ]
                previous_snapshot = self._latest_snapshot_locked(competitor)

            raw_snapshot = self.compare_agent.build_snapshot(
                competitor,
                cards,
                product_version=product_version,
                benchmark_tasks=benchmark_tasks,
                benchmark_runs=benchmark_runs,
                previous_snapshot=previous_snapshot,
            )
            snapshot = (
                raw_snapshot
                if isinstance(raw_snapshot, CapabilitySnapshot)
                else CapabilitySnapshot.model_validate(raw_snapshot)
            )

            with self._lock:
                existing = self._snapshots.get(snapshot.snapshot_id)
                if existing is not None:
                    predecessor = (
                        self._snapshots.get(existing.previous_snapshot_id)
                        if existing.previous_snapshot_id
                        else None
                    )
                    return existing, predecessor, cards
                self._snapshots[snapshot.snapshot_id] = snapshot
            return snapshot, previous_snapshot, cards

    def _latest_snapshot_locked(
        self,
        competitor: str,
    ) -> CapabilitySnapshot | None:
        target = competitor.strip().casefold()
        matches = [
            (position, snapshot)
            for position, snapshot in enumerate(self._snapshots.values())
            if snapshot.competitor.strip().casefold() == target
        ]
        if not matches:
            return None
        return max(
            matches,
            key=lambda item: (item[1].snapshot_date, item[0]),
        )[1]


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
