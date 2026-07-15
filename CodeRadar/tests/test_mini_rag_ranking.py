from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mini_rag.models import Chunk, RAGQuery, SearchCandidate
from mini_rag.ranking import (
    CrossEncoderReranker,
    EvidenceRanker,
    RankingPipeline,
    TemporalVersionRanker,
    TermOverlapReranker,
    normalise_version,
    rrf_fuse,
)


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def make_chunk(
    chunk_id: str,
    content: str,
    *,
    evidence_level: str = "A",
    version: str | None = "1.0.0",
    published: datetime = NOW,
    current: bool = True,
    metadata: dict | None = None,
) -> Chunk:
    return Chunk(
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
        competitor="Cursor",
        source_type="official_changelog",
        evidence_level=evidence_level,
        url=f"https://example.com/{chunk_id}",
        product_version=version,
        publish_time=published,
        crawl_time=NOW,
        valid_from=published,
        is_current=current,
        source_metadata=metadata or {},
    )


def candidate(chunk_id: str, content: str, **kwargs) -> SearchCandidate:
    return SearchCandidate(chunk=make_chunk(chunk_id, content, **kwargs))


def test_rrf_fuses_duplicates_and_preserves_both_ranks() -> None:
    alpha = candidate("alpha", "Agent context")
    beta = candidate("beta", "Pricing")
    gamma = candidate("gamma", "Security")

    fused = rrf_fuse([alpha, beta], [gamma, alpha], k=60)

    assert [item.chunk_id for item in fused] == ["alpha", "gamma", "beta"]
    assert fused[0].bm25_rank == 1
    assert fused[0].dense_rank == 2
    assert fused[0].rrf_score == pytest.approx(1 / 61 + 1 / 62)
    assert fused[1].rrf_score == pytest.approx(1 / 61)


def test_rrf_rejects_invalid_windows_and_negative_weights() -> None:
    item = candidate("alpha", "Agent")
    with pytest.raises(ValueError):
        rrf_fuse([item], [], top_k=0)
    with pytest.raises(ValueError):
        rrf_fuse([item], [], bm25_weight=-1)


def test_term_overlap_reranker_supports_chinese_and_exact_versions() -> None:
    candidates = [
        SearchCandidate(chunk=make_chunk("match", "Agent 上下文窗口扩展到 1.2.0"), rrf_score=0.02),
        SearchCandidate(chunk=make_chunk("other", "团队套餐价格调整"), rrf_score=0.04),
    ]

    ranked = TermOverlapReranker().rerank("Agent 上下文 1.2.0", candidates)

    assert ranked[0].chunk_id == "match"
    assert ranked[0].rerank_score > ranked[1].rerank_score


def test_cross_encoder_uses_injected_predictor_and_normalises_scores() -> None:
    class FakeModel:
        def predict(self, pairs, batch_size):
            assert batch_size == 2
            return [-2.0, 3.0]

    candidates = [candidate("first", "first"), candidate("second", "second")]
    reranker = CrossEncoderReranker(model=FakeModel(), batch_size=2)

    ranked = reranker.rerank("question", candidates)

    assert reranker.last_backend == "cross_encoder"
    assert ranked[0].chunk_id == "second"
    assert ranked[0].rerank_score == pytest.approx(1.0)
    assert ranked[1].rerank_score == pytest.approx(0.0)


def test_cross_encoder_failure_uses_offline_lexical_fallback() -> None:
    def broken_scorer(query, texts):
        raise RuntimeError("offline")

    reranker = CrossEncoderReranker(scorer=broken_scorer)
    ranked = reranker.rerank(
        "security",
        [candidate("pricing", "pricing"), candidate("security", "security compliance")],
    )

    assert reranker.last_backend == "term_overlap"
    assert "offline" in (reranker.last_error or "")
    assert ranked[0].chunk_id == "security"


def test_temporal_version_ranker_filters_current_version_and_ranks_recent_first() -> None:
    recent = SearchCandidate(
        chunk=make_chunk("recent", "new", version="v1.2.0", published=NOW - timedelta(days=2)),
        rerank_score=0.8,
    )
    old = SearchCandidate(
        chunk=make_chunk(
            "old",
            "old",
            version="1.2",
            published=NOW - timedelta(days=500),
            current=False,
        ),
        rerank_score=0.8,
    )
    other = SearchCandidate(
        chunk=make_chunk("other", "other", version="2.0", published=NOW),
        rerank_score=1.0,
    )
    query = RAGQuery(question="Cursor 当前版本", current_only=True, product_versions=["1.2.0"])

    ranked = TemporalVersionRanker().rerank([old, other, recent], query, now=NOW)

    assert [item.chunk_id for item in ranked] == ["recent"]
    assert ranked[0].temporal_score is not None
    assert ranked[0].version_score == 1.0
    assert normalise_version("Version v1.2.0") == "1.2"


def test_temporal_ranker_honours_as_of_effective_interval() -> None:
    current = make_chunk("current", "current", published=NOW, current=True)
    historical = make_chunk("historical", "historical", published=NOW - timedelta(days=90), current=False)
    historical.valid_to = NOW - timedelta(days=10)
    candidates = [SearchCandidate(chunk=current), SearchCandidate(chunk=historical)]

    ranked = TemporalVersionRanker().rerank(
        candidates,
        "历史价格",
        as_of=NOW - timedelta(days=20),
        now=NOW,
    )

    assert [item.chunk_id for item in ranked] == ["historical"]


def test_historical_intent_prefers_non_current_versions_without_as_of() -> None:
    current = SearchCandidate(
        chunk=make_chunk("current", "same", published=NOW, current=True),
        rerank_score=0.8,
    )
    historical = SearchCandidate(
        chunk=make_chunk(
            "historical",
            "same",
            published=NOW - timedelta(days=90),
            current=False,
        ),
        rerank_score=0.8,
    )

    ranked = TemporalVersionRanker().rerank(
        [current, historical],
        "previous version history",
        now=NOW,
    )

    assert ranked[0].chunk_id == "historical"
    assert ranked[0].version_score == 1.0


def test_evidence_ranker_breaks_relevance_tie_by_authority() -> None:
    low = SearchCandidate(chunk=make_chunk("low", "same", evidence_level="D"), final_score=0.5)
    high = SearchCandidate(chunk=make_chunk("high", "same", evidence_level="A"), final_score=0.5)

    ranked = EvidenceRanker(evidence_weight=0.5).rerank([low, high])

    assert ranked[0].chunk_id == "high"
    assert ranked[0].evidence_score > ranked[1].evidence_score


def test_full_ranking_pipeline_returns_requested_top_k() -> None:
    pipeline = RankingPipeline(reranker=TermOverlapReranker())
    results = pipeline.rank(
        "Agent context",
        [candidate("one", "Agent context"), candidate("two", "pricing"), candidate("three", "security")],
        top_k=2,
        now=NOW,
    )

    assert len(results) == 2
    assert results[0].chunk_id == "one"


def test_pipeline_applies_exact_four_component_weights() -> None:
    class FixedReranker:
        def rerank(self, question, candidates):
            return [
                item.model_copy(update={"rerank_score": score, "final_score": score})
                for item, score in zip(candidates, (0.2, 0.8))
            ]

    class FixedTemporalRanker:
        def rerank(self, candidates, query, **options):
            values = ((0.4, 0.6), (0.3, 0.7))
            return [
                item.model_copy(
                    update={
                        "temporal_score": temporal,
                        "version_score": version,
                        "final_score": 0.0,
                    }
                )
                for item, (temporal, version) in zip(candidates, values)
            ]

    class FixedEvidenceRanker:
        def rerank(self, candidates):
            return [
                item.model_copy(update={"evidence_score": score, "final_score": 0.0})
                for item, score in zip(candidates, (0.5, 0.9))
            ]

    pipeline = RankingPipeline(
        reranker=FixedReranker(),
        temporal_ranker=FixedTemporalRanker(),
        evidence_ranker=FixedEvidenceRanker(),
        semantic_weight=0.7,
        temporal_weight=0.1,
        version_weight=0.1,
        evidence_weight=0.1,
    )
    results = pipeline.rank(
        "question",
        [candidate("first", "first"), candidate("second", "second")],
    )

    # Reranker scores normalise to 0 and 1 before exact component weighting.
    assert results[0].chunk_id == "second"
    assert results[0].final_score == pytest.approx(0.7 * 1.0 + 0.1 * 0.3 + 0.1 * 0.7 + 0.1 * 0.9)
    assert results[1].final_score == pytest.approx(0.7 * 0.0 + 0.1 * 0.4 + 0.1 * 0.6 + 0.1 * 0.5)
    assert results[0].metadata["semantic_score"] == 1.0


def test_pipeline_rejects_weights_that_do_not_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        RankingPipeline(semantic_weight=0.7, temporal_weight=0.2, version_weight=0.2, evidence_weight=0.1)
