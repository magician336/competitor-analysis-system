from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.compare_agent import CompareAgent
from schemas.benchmark import BenchmarkRun, BenchmarkTask
from schemas.capability_snapshot import CapabilityScoreStatus
from schemas.document import DimensionTag, EventType, EvidenceLevel, SourceType
from schemas.intelligence_card import (
    AgentKind,
    CapabilityImpact,
    EvidenceReference,
    ImpactDirection,
    IntelligenceCard,
)


def _card(
    *,
    card_id: str,
    chunk_id: str,
    dimension: DimensionTag,
    direction: ImpactDirection,
    magnitude: int,
    agent_kind: AgentKind = AgentKind.PRODUCT,
) -> IntelligenceCard:
    event_type = {
        AgentKind.PRICE: EventType.PRICING_CHANGE,
        AgentKind.PRODUCT: EventType.PRODUCT_RELEASE,
        AgentKind.RISK: EventType.RISK_EXPERIENCE,
    }[agent_kind]
    return IntelligenceCard(
        card_id=card_id,
        agent_kind=agent_kind,
        competitor="Cursor",
        event_type=event_type,
        dimension_tags=[dimension],
        event_title="Evidence-backed change",
        summary="The source records a measurable competitor change.",
        impact_analysis="The signed impact is used by the snapshot scorer.",
        relevance_to_our_product="high",
        opportunity="Validate a differentiated response.",
        threat="The change may alter the competitive gap.",
        recommended_action="Review the cited source and benchmark the capability.",
        confidence_score=0.9,
        priority_score=75,
        evidence=[
            EvidenceReference(
                chunk_id=chunk_id,
                document_id=f"doc_{chunk_id}",
                version_id=f"ver_{chunk_id}",
                title="Official source",
                url="https://example.test/source",
                competitor="Cursor",
                source_type=SourceType.OFFICIAL_CHANGELOG,
                evidence_level=EvidenceLevel.A,
                event_type=event_type,
                dimension_tags=[dimension],
                publish_time=datetime.now(timezone.utc),
                quote="Verified source excerpt.",
            )
        ],
        impact_details=[
            CapabilityImpact(
                dimension=dimension,
                direction=direction,
                magnitude=magnitude,
                confidence_score=0.9,
                rationale="The cited release or risk report supports this direction.",
                evidence_chunk_ids=[chunk_id],
            )
        ],
    )


def test_negative_risk_evidence_lowers_score_and_missing_dimensions_are_explicit() -> None:
    snapshot = CompareAgent().build_snapshot(
        "Cursor",
        [
            _card(
                card_id="card_risk",
                chunk_id="chunk_risk",
                dimension=DimensionTag.SECURITY_COMPLIANCE,
                direction=ImpactDirection.NEGATIVE,
                magnitude=8,
                agent_kind=AgentKind.RISK,
            )
        ],
    )

    security = next(
        item
        for item in snapshot.details
        if item.dimension == DimensionTag.SECURITY_COMPLIANCE
    )
    code = next(
        item
        for item in snapshot.details
        if item.dimension == DimensionTag.CODE_INTELLIGENCE
    )
    assert security.status == CapabilityScoreStatus.SCORED
    assert security.score < 50
    assert code.status == CapabilityScoreStatus.INSUFFICIENT_EVIDENCE
    assert code.score == 0
    assert snapshot.coverage_ratio == 0.12


def test_same_chunk_is_counted_once_even_when_repeated_across_cards() -> None:
    first = _card(
        card_id="card_first",
        chunk_id="chunk_shared",
        dimension=DimensionTag.AGENT_CONTEXT,
        direction=ImpactDirection.POSITIVE,
        magnitude=5,
    )
    stronger = _card(
        card_id="card_second",
        chunk_id="chunk_shared",
        dimension=DimensionTag.AGENT_CONTEXT,
        direction=ImpactDirection.POSITIVE,
        magnitude=8,
    )

    snapshot = CompareAgent().build_snapshot("Cursor", [first, first, stronger])
    detail = next(
        item for item in snapshot.details if item.dimension == DimensionTag.AGENT_CONTEXT
    )

    assert detail.evidence_count == 1
    assert detail.evidence_chunk_ids == ["chunk_shared"]
    assert len(detail.evidence_contributions) == 1
    assert detail.evidence_contributions[0].magnitude == 8


def test_benchmark_data_contributes_and_previous_snapshot_produces_delta() -> None:
    task = BenchmarkTask(
        task_id="bench_compare",
        name="Fixed completion task",
        task_type="completion",
        language="Python",
        prompt="Complete the function.",
        expected_behavior="All fixed assertions pass.",
        primary_dimensions=[DimensionTag.CODE_INTELLIGENCE],
    )
    weak_run = BenchmarkRun(
        run_id="run_weak",
        competitor="Cursor",
        task_id=task.task_id,
        task_revision=task.task_revision,
        task_fingerprint=task.task_fingerprint,
        validator_sha256="1" * 64,
        protocol_sha256="2" * 64,
        starter_sha256="3" * 64,
        candidate_sha256="4" * 64,
        task_success=False,
        compile_success=False,
        test_pass_rate=0.2,
        manual_intervention=3,
        harmful_action=False,
    )
    strong_run = BenchmarkRun(
        run_id="run_strong",
        competitor="Cursor",
        task_id=task.task_id,
        task_revision=task.task_revision,
        task_fingerprint=task.task_fingerprint,
        validator_sha256="1" * 64,
        protocol_sha256="2" * 64,
        starter_sha256="3" * 64,
        candidate_sha256="5" * 64,
        task_success=True,
        compile_success=True,
        test_pass_rate=1.0,
        manual_intervention=0,
        harmful_action=False,
    )
    agent = CompareAgent()
    previous = agent.build_snapshot(
        "Cursor",
        [],
        benchmark_tasks=[task],
        benchmark_runs=[weak_run],
    )
    current = agent.build_snapshot(
        "Cursor",
        [],
        benchmark_tasks=[task],
        benchmark_runs=[strong_run, strong_run],
        previous_snapshot=previous,
    )
    detail = next(
        item
        for item in current.details
        if item.dimension == DimensionTag.CODE_INTELLIGENCE
    )

    assert detail.benchmark_score == 100.0
    assert detail.benchmark_run_ids == ["run_strong"]
    assert detail.delta is not None and detail.delta > 0
    assert current.previous_snapshot_id == previous.snapshot_id


def test_snapshot_rejects_mixed_benchmark_protocols_for_the_same_task() -> None:
    task = BenchmarkTask(
        task_id="bench_protocol_mix",
        name="Protocol consistency fixture",
        task_type="completion",
        language="Python",
        prompt="Complete the deterministic function.",
        expected_behavior="All fixed assertions pass.",
        primary_dimensions=[DimensionTag.CODE_INTELLIGENCE],
    )
    common = {
        "competitor": "Cursor",
        "task_id": task.task_id,
        "task_revision": task.task_revision,
        "task_fingerprint": task.task_fingerprint,
        "validator_sha256": "1" * 64,
        "starter_sha256": "3" * 64,
        "task_success": True,
        "test_pass_rate": 1.0,
    }
    first = BenchmarkRun(
        run_id="run_protocol_one",
        protocol_sha256="2" * 64,
        candidate_sha256="4" * 64,
        **common,
    )
    second = BenchmarkRun(
        run_id="run_protocol_two",
        protocol_sha256="9" * 64,
        candidate_sha256="5" * 64,
        **common,
    )

    with pytest.raises(ValueError, match="mixes validator/protocol/starter"):
        CompareAgent().build_snapshot(
            "Cursor",
            [],
            benchmark_tasks=[task],
            benchmark_runs=[first, second],
        )
