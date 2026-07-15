from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from mini_rag.models import (
    CitationValidationResult,
    Conflict,
    EvaluationCase,
    Evidence,
    RAGQuery,
    RAGResponse,
    SearchCandidate,
)
from mini_rag.chunking import chunk_document
from schemas.document import StructuredDocument


def _chunk():
    document = StructuredDocument(
        raw_record_id="raw_contract",
        raw_path="data/raw/contract.txt",
        competitor="Cursor",
        title="Agent update",
        content="# Agent\n\nRepository context is available.",
        source_type="changelog",
        evidence_level="A",
        url="https://example.test/changelog/agent",
        crawl_time="2026-07-14T00:00:00Z",
        publish_time="2026-07-01T00:00:00Z",
        event_type="E2",
        dimension_tags=["D2"],
    )
    return chunk_document(document)[0]


def test_shared_contract_round_trip_from_candidate_to_response() -> None:
    chunk = _chunk()
    candidate = SearchCandidate(
        chunk=chunk,
        bm25_rank=1,
        dense_rank=2,
        rrf_score=0.03,
        rerank_score=0.91,
        final_score=0.88,
        retrieval_methods=["BM25", "dense", "bm25"],
    )

    evidence = Evidence.from_candidate(candidate)
    response = RAGResponse(
        query="Cursor Agent context",
        parsed_filters={"competitor": "Cursor"},
        evidence=[evidence],
    )
    restored = RAGResponse.model_validate_json(response.model_dump_json())

    assert candidate.retrieval_methods == ["bm25", "dense"]
    assert evidence.chunk_id == chunk.chunk_id
    assert evidence.quote == chunk.content
    assert evidence.bm25_rank == 1
    assert restored.query_id.startswith("qry_")
    assert restored.retrieval_trace.returned_candidates == 1


def test_rag_query_normalises_filters_and_validates_time_range() -> None:
    query = RAGQuery(
        question="  latest Agent update  ",
        competitor=" Cursor ",
        event_types=["E2", "product_release"],
        dimension_tags=["D2", "agent_context"],
        evidence_levels=["a", "A"],
        current_only=True,
    )

    assert query.question == "latest Agent update"
    assert query.competitor == "Cursor"
    assert len(query.event_types) == len(query.dimension_tags) == 1
    assert query.filters()["current_only"] is True
    with pytest.raises(ValidationError, match="end_time"):
        RAGQuery(
            question="history",
            start_time="2026-07-02T00:00:00Z",
            end_time="2026-07-01T00:00:00Z",
        )


def test_conflict_citation_and_evaluation_contracts_enforce_invariants() -> None:
    chunk = _chunk()
    conflict = Conflict(
        field="price_value",
        competitor="Cursor",
        values=["20", "25"],
        chunk_ids=[chunk.chunk_id, "chunk_other"],
        preferred_chunk_id=chunk.chunk_id,
    )
    citation_result = CitationValidationResult(
        valid=True,
        errors=["quote mismatch", "quote mismatch"],
        validated_chunk_ids=[chunk.chunk_id, chunk.chunk_id],
        conflicts=[conflict],
    )
    case = EvaluationCase(
        question="What is the current price?",
        expected_document_ids=[chunk.document_id],
        expected_chunk_ids=[chunk.chunk_id],
        expected_competitor="Cursor",
    )

    assert conflict.conflict_id.startswith("conflict_")
    assert citation_result.valid is False
    assert citation_result.errors == ["quote mismatch"]
    assert citation_result.validated_chunk_ids == [chunk.chunk_id]
    assert case.case_id.startswith("case_")
    assert case.expected_document_ids == [chunk.document_id]
    assert case.expected_chunk_ids == [chunk.chunk_id]
    assert case.model_dump(mode="json")["relevant_chunk_ids"] == [chunk.chunk_id]

    with pytest.raises(ValidationError, match="preferred_chunk_id"):
        Conflict(
            field="price_value",
            values=["20", "25"],
            chunk_ids=["a", "b"],
            preferred_chunk_id="c",
        )
