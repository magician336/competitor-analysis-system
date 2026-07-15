from __future__ import annotations

from mini_rag.models import EvaluationCase
from scripts.validate_evaluation_set import validate_indexed_cases


def test_validate_indexed_cases_checks_traceability_filters_and_quote() -> None:
    case = EvaluationCase(
        case_id="cursor-agent",
        question="Cursor Agent",
        relevant_document_ids=["doc-1"],
        relevant_chunk_ids=["chunk-1"],
        relevance_grades={"chunk-1": 3},
        expected_evidence_quote="repository context",
        query_filters={
            "competitor": "Cursor",
            "source_types": ["official_changelog"],
            "current_only": True,
        },
    )
    chunks = {
        "chunk-1": {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "competitor": "Cursor",
            "source_type": "official_changelog",
            "is_current": True,
            "content": "Agent reads repository context before editing.",
        }
    }

    result = validate_indexed_cases([case], get_chunk=chunks.get)

    assert result == {
        "valid": True,
        "case_count": 1,
        "checked_chunk_count": 1,
        "errors": [],
    }
