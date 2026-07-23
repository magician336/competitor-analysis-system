from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.document import DimensionTag, EventType, EvidenceLevel, SourceType
from schemas.intelligence_card import (
    AgentKind,
    CapabilityImpact,
    EvidenceReference,
    ImpactDirection,
    IntelligenceCard,
)


def _evidence(
    *,
    competitor: str = "Cursor",
    event_type: EventType | None = EventType.PRODUCT_RELEASE,
) -> EvidenceReference:
    return EvidenceReference(
        chunk_id="chunk_contract_001",
        document_id="document_contract_001",
        version_id="version_contract_001",
        title="Product release",
        url="https://example.com/changelog",
        competitor=competitor,
        source_type=SourceType.OFFICIAL_CHANGELOG,
        evidence_level=EvidenceLevel.A,
        event_type=event_type,
        dimension_tags=[DimensionTag.AGENT_CONTEXT],
        quote="The release adds repository-level planning.",
    )


def _impact(*, citations: list[str] | None = None) -> CapabilityImpact:
    return CapabilityImpact(
        dimension=DimensionTag.AGENT_CONTEXT,
        direction=ImpactDirection.POSITIVE,
        magnitude=7,
        confidence_score=0.85,
        rationale="Repository-level planning improves project context handling.",
        evidence_chunk_ids=(
            ["chunk_contract_001"] if citations is None else citations
        ),
    )


def _card_payload() -> dict[str, object]:
    return {
        "agent_kind": AgentKind.PRODUCT,
        "competitor": "Cursor",
        "event_type": EventType.PRODUCT_RELEASE,
        "dimension_tags": [],
        "event_title": "Repository Agent release",
        "summary": "A repository-level planning capability was released.",
        "impact_analysis": "The cited release affects project context handling.",
        "relevance_to_our_product": "Relevant to CodeMate Campus project tasks.",
        "opportunity": "Strengthen guided project planning.",
        "threat": "Competitors may improve autonomous project execution.",
        "recommended_action": "Evaluate the release with a fixed task.",
        "confidence_score": 0.85,
        "priority_score": 70,
        "evidence": [_evidence()],
        "impact_details": [_impact()],
    }


def test_card_accepts_case_insensitive_evidence_competitor_and_merges_dimension() -> None:
    payload = _card_payload()
    payload["competitor"] = "cursor"

    card = IntelligenceCard.model_validate(payload)

    assert card.dimension_tags == [DimensionTag.AGENT_CONTEXT]


def test_card_rejects_cross_competitor_evidence() -> None:
    payload = _card_payload()
    payload["evidence"] = [_evidence(competitor="GitHub Copilot")]

    with pytest.raises(ValidationError, match="evidence competitor must match"):
        IntelligenceCard.model_validate(payload)


def test_card_rejects_cross_event_evidence() -> None:
    payload = _card_payload()
    payload["evidence"] = [_evidence(event_type=EventType.PRICING_CHANGE)]

    with pytest.raises(ValidationError, match="evidence event_type must match"):
        IntelligenceCard.model_validate(payload)


def test_evidenced_card_rejects_uncited_capability_impact() -> None:
    payload = _card_payload()
    payload["impact_details"] = [_impact(citations=[])]

    with pytest.raises(ValidationError, match="every capability impact"):
        IntelligenceCard.model_validate(payload)


def test_degraded_card_may_keep_uncited_impact_without_evidence() -> None:
    payload = _card_payload()
    payload["evidence"] = []
    payload["impact_details"] = [_impact(citations=[])]

    card = IntelligenceCard.model_validate(payload)

    assert card.review_required is True
    assert card.dimension_tags == [DimensionTag.AGENT_CONTEXT]
