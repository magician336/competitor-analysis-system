from __future__ import annotations

from datetime import datetime, timezone

from agents import ProductAgent
from mini_rag.models import Conflict, Evidence, RAGQuery, RAGResponse, RetrievalTrace
from schemas.document import DimensionTag, EventType, EvidenceLevel, SourceType
from schemas.intelligence_card import AgentAnalysisRequest


class EventRAGService:
    def __init__(
        self,
        evidence: list[Evidence],
        conflicts: list[Conflict] | None = None,
    ) -> None:
        self.evidence = evidence
        self.conflicts = conflicts or []

    def query(self, request: RAGQuery) -> RAGResponse:
        return RAGResponse(
            query=request.question,
            parsed_filters=request.filters(),
            evidence=[item.model_copy(deep=True) for item in self.evidence],
            conflicts=[item.model_copy(deep=True) for item in self.conflicts],
            retrieval_trace=RetrievalTrace(
                returned_candidates=len(self.evidence),
                latency_ms=0.1,
            ),
        )


def _evidence(
    chunk_id: str,
    *,
    document_id: str,
    version_id: str,
    title: str,
    dimension: DimensionTag = DimensionTag.AGENT_CONTEXT,
) -> Evidence:
    content = f"{title} adds a cited product capability in {chunk_id}."
    return Evidence(
        chunk_id=chunk_id,
        document_id=document_id,
        version_id=version_id,
        content=content,
        quote=content,
        title=title,
        url=f"https://example.test/changelog/{document_id}/{version_id}",
        char_start=0,
        char_end=len(content),
        competitor="Cursor",
        source_type=SourceType.OFFICIAL_CHANGELOG,
        evidence_level=EvidenceLevel.A,
        event_type=EventType.PRODUCT_RELEASE,
        dimension_tags=[dimension],
        publish_time=datetime(2026, 7, 20, tzinfo=timezone.utc),
        final_score=0.9,
    )


def _run(
    evidence: list[Evidence],
    *,
    conflicts: list[Conflict] | None = None,
    max_cards: int = 5,
):
    return ProductAgent(
        EventRAGService(evidence, conflicts),
        auto_configure_llm=False,
    ).run(
        AgentAnalysisRequest(
            competitor="Cursor",
            max_cards=max_cards,
            top_k=20,
        )
    )


def test_same_document_version_chunks_merge_into_one_event_card() -> None:
    first = _evidence(
        "chunk_event_a_1",
        document_id="doc_event_a",
        version_id="version_1",
        title="Repository Agent",
    )
    second = _evidence(
        "chunk_event_a_2",
        document_id="doc_event_a",
        version_id="version_1",
        title="Repository Agent details",
    )

    result = _run([first, second])

    assert len(result.cards) == 1
    assert [item.chunk_id for item in result.cards[0].evidence] == [
        first.chunk_id,
        second.chunk_id,
    ]
    assert result.evidence_count == 2
    assert len(result.cards[0].findings) == 2


def test_different_documents_or_versions_split_cards_in_retrieval_order() -> None:
    ranked = [
        _evidence(
            "chunk_rank_1",
            document_id="doc_alpha",
            version_id="version_1",
            title="First ranked event",
        ),
        _evidence(
            "chunk_rank_2",
            document_id="doc_beta",
            version_id="version_1",
            title="Second ranked event",
        ),
        _evidence(
            "chunk_rank_3",
            document_id="doc_alpha",
            version_id="version_2",
            title="New version of first document",
        ),
    ]

    result = _run(ranked)

    assert len(result.cards) == 3
    assert [card.evidence[0].chunk_id for card in result.cards] == [
        "chunk_rank_1",
        "chunk_rank_2",
        "chunk_rank_3",
    ]
    assert result.evidence_count == 3
    assert all(len(card.evidence) == 1 for card in result.cards)


def test_max_cards_keeps_the_first_ranked_event_clusters() -> None:
    ranked = [
        _evidence(
            f"chunk_limit_{index}",
            document_id=f"doc_limit_{index}",
            version_id="version_1",
            title=f"Ranked event {index}",
        )
        for index in range(1, 5)
    ]

    result = _run(ranked, max_cards=2)

    assert [card.evidence[0].chunk_id for card in result.cards] == [
        "chunk_limit_1",
        "chunk_limit_2",
    ]
    assert result.evidence_count == 2
    assert any("retained 2 of 4" in warning for warning in result.warnings)


def test_conflict_is_attached_only_to_cards_containing_involved_chunks() -> None:
    first = _evidence(
        "chunk_conflict_1",
        document_id="doc_conflict_1",
        version_id="version_1",
        title="First conflicting event",
    )
    unrelated = _evidence(
        "chunk_unrelated",
        document_id="doc_unrelated",
        version_id="version_1",
        title="Unrelated event",
    )
    third = _evidence(
        "chunk_conflict_3",
        document_id="doc_conflict_3",
        version_id="version_1",
        title="Third conflicting event",
    )
    conflict = Conflict(
        field="release_status",
        competitor="Cursor",
        values=["preview", "general_availability"],
        chunk_ids=[first.chunk_id, third.chunk_id],
        reason="Two source versions report different release states.",
    )

    result = _run([first, unrelated, third], conflicts=[conflict])

    by_chunk = {card.evidence[0].chunk_id: card for card in result.cards}
    assert len(by_chunk[first.chunk_id].conflict_notes) == 1
    assert by_chunk[first.chunk_id].review_required is True
    assert by_chunk[unrelated.chunk_id].conflict_notes == []
    assert len(by_chunk[third.chunk_id].conflict_notes) == 1
    assert by_chunk[third.chunk_id].review_required is True


def test_no_evidence_still_returns_one_degraded_card() -> None:
    result = _run([])

    assert len(result.cards) == 1
    assert result.cards[0].evidence == []
    assert result.cards[0].review_required is True
    assert result.evidence_count == 0
    assert result.degraded is True
