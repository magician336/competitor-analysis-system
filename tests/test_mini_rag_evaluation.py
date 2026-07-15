from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from mini_rag.evaluation import (
    ABLATION_SPECS,
    AblationRunner,
    RetrievalEvaluator,
    citation_accuracy,
    conflict_detection_metrics,
    latency_metrics,
    load_evaluation_cases,
    validate_evaluation_cases,
    metadata_filter_accuracy,
    ndcg_at_k,
    old_version_false_recall_rate,
    recall_at_k,
    reciprocal_rank,
)
from mini_rag.evidence import CitationBuilder
from mini_rag.models import (
    Chunk,
    Conflict,
    EvaluationCase,
    RAGResponse,
    RetrievalTrace,
    SearchCandidate,
)


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def make_candidate(chunk_id: str, *, current: bool = True, competitor: str = "Cursor") -> SearchCandidate:
    content = f"Evidence for {chunk_id}"
    return SearchCandidate(
        chunk=Chunk(
            chunk_id=chunk_id,
            document_id=f"doc-{chunk_id}",
            version_id=f"ver-{chunk_id}",
            raw_record_id=f"raw-{chunk_id}",
            raw_path=f"raw/{chunk_id}.html",
            chunk_index=0,
            title=f"Title {chunk_id}",
            content=content,
            char_start=0,
            char_end=len(content),
            competitor=competitor,
            source_type="official_page",
            evidence_level="A",
            url=f"https://example.com/{chunk_id}",
            product_version="1.2.0",
            publish_time=NOW,
            crawl_time=NOW,
            valid_from=NOW,
            is_current=current,
            event_type="product_release",
            dimension_tags=["agent_context"],
        ),
        final_score=1.0,
    )


def test_retrieval_metrics_have_standard_values() -> None:
    retrieved = ["x", "b", "a", "z"]
    relevant = ["a", "b"]

    assert recall_at_k(retrieved, relevant, 2) == pytest.approx(0.5)
    assert recall_at_k(retrieved, relevant, 3) == pytest.approx(1.0)
    assert reciprocal_rank(retrieved, relevant) == pytest.approx(0.5)
    assert ndcg_at_k(["a", "b"], {"a": 3, "b": 1}, 2) == pytest.approx(1.0)
    assert ndcg_at_k(["b", "a"], {"a": 3, "b": 1}, 2) < 1.0


def test_filter_citation_version_and_latency_metrics() -> None:
    current = make_candidate("current")
    stale = make_candidate("stale", current=False)
    expected = {
        "competitor": "Cursor",
        "event_types": ["product_release"],
        "dimension_tags": ["agent_context"],
        "product_versions": ["v1.2"],
        "current_only": True,
    }

    assert metadata_filter_accuracy([current], expected) == 1.0
    assert metadata_filter_accuracy([current, stale], expected) == 0.5
    assert old_version_false_recall_rate([current, stale]) == 0.5
    assert citation_accuracy([True, False, True]) == pytest.approx(2 / 3)
    latency = latency_metrics([1, 2, 3, 4])
    assert latency["mean_latency_ms"] == pytest.approx(2.5)
    assert latency["p95_latency_ms"] == pytest.approx(3.85)


def test_conflict_detection_metrics_compare_field_and_value_set() -> None:
    predicted = [
        Conflict(field="price", values=["18", "20"], chunk_ids=["a", "b"])
    ]
    expected = [
        {"field": "price", "values": ["20", "18"], "chunk_ids": ["x", "y"]}
    ]

    metrics = conflict_detection_metrics(predicted, expected)

    assert metrics["conflict_precision"] == 1.0
    assert metrics["conflict_recall"] == 1.0
    assert metrics["conflict_detection_accuracy"] == 1.0


def test_evaluator_calls_public_query_contract_and_aggregates_metrics() -> None:
    evidence = CitationBuilder().build([make_candidate("relevant"), make_candidate("other")])

    def query(query):
        assert query.question == "Agent update"
        assert query.current_only
        return RAGResponse(
            query=query.question,
            parsed_filters=query.filters(),
            evidence=evidence,
            retrieval_trace=RetrievalTrace(returned_candidates=2),
        )

    result = RetrievalEvaluator(query).evaluate(
        [
            {
                "question": "Agent update",
                "relevant_chunk_ids": ["relevant"],
                "relevance_grades": {"relevant": 2, "other": 0},
                "expected_competitor": "Cursor",
                "expected_event_type": "product_release",
                "expected_dimension_tags": ["agent_context"],
                "expected_version": "1.2.0",
                "expected_evidence_quote": "Evidence for relevant",
                "query_filters": {"current_only": True},
            }
        ],
        config={"index": "fixture"},
    )

    assert result.case_count == 1
    assert result.metrics["recall@5"] == 1.0
    assert result.metrics["mrr"] == 1.0
    assert result.metrics["ndcg@10"] == 1.0
    assert result.metrics["metadata_filter_accuracy"] == 1.0
    assert result.metrics["citation_accuracy"] == 1.0
    assert result.metrics["query_success_rate"] == 1.0
    assert result.metrics["p95_latency_ms"] >= 0
    assert result.config["index"] == "fixture"


def test_evaluator_records_query_failures_without_aborting() -> None:
    def query(_query):
        raise RuntimeError("index unavailable")

    result = RetrievalEvaluator(query).evaluate(
        [{"question": "question", "relevant_chunk_ids": ["missing"]}]
    )

    assert result.metrics["query_success_rate"] == 0.0
    assert result.details[0].errors == ["RuntimeError: index unavailable"]
    assert result.details[0].metrics["recall@5"] == 0.0


def test_load_evaluation_cases_supports_documented_csv(tmp_path) -> None:
    dataset = tmp_path / "cases.csv"
    dataset.write_text(
        "question,expected_chunk_ids,expected_dimension_tags,relevance_grades,query_filters\n"
        'Agent update,"[""chunk-1""]","agent_context","{""chunk-1"": 2}","{""current_only"": true}"\n',
        encoding="utf-8",
    )

    cases = load_evaluation_cases(dataset)

    assert len(cases) == 1
    assert cases[0].relevant_chunk_ids == ["chunk-1"]
    assert cases[0].relevance_grades == {"chunk-1": 2.0}
    assert cases[0].query_filters == {"current_only": True}


def test_evaluation_dataset_validation_rejects_unsupported_or_ambiguous_gold() -> None:
    valid = [
        {
            "case_id": "agent",
            "question": "Agent update",
            "relevant_chunk_ids": ["chunk-1"],
            "relevance_grades": {"chunk-1": 3},
            "query_filters": {"competitor": "Cursor", "current_only": True},
        }
    ]
    cases = [EvaluationCase.model_validate(item) for item in valid]

    validate_evaluation_cases(cases)

    with pytest.raises(ValueError, match="duplicate evaluation case_id"):
        validate_evaluation_cases(cases + cases)
    no_answer = cases[0].model_copy(
        update={"case_id": "no-answer", "relevant_chunk_ids": []}
    )
    with pytest.raises(ValueError, match="no relevant IDs"):
        validate_evaluation_cases([no_answer])
    unknown_filter = cases[0].model_copy(
        update={"case_id": "unknown", "query_filters": {"typo_filter": True}}
    )
    with pytest.raises(ValueError, match="unknown query filters"):
        validate_evaluation_cases([unknown_filter])


def test_all_six_ablation_groups_run_with_shared_cases() -> None:
    evidence = CitationBuilder().build([make_candidate("relevant")])
    seen: list[str] = []

    def factory(spec):
        seen.append(spec.group)
        return RetrievalEvaluator(lambda query: RAGResponse(query=query.question, evidence=evidence))

    results = AblationRunner(factory).run(
        [{"question": "question", "relevant_chunk_ids": ["relevant"]}],
        shared_config={"hardware": "cpu"},
    )

    assert seen == ["A", "B", "C", "D", "E", "F"]
    assert set(results) == {spec.group for spec in ABLATION_SPECS}
    assert all(result.metrics["recall@5"] == 1.0 for result in results.values())
    assert results["F"].config["hardware"] == "cpu"
    assert results["F"].config["ablation"]["use_evidence_ranking"] is True
