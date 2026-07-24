from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from mini_rag.embedding import EmbeddingService, HashEmbedding
from mini_rag.indexing import (
    InMemorySearchBackend,
    IndexBuilder,
    IndexManager,
    build_filter_clauses,
    build_index_mapping,
    embedding_metadata,
)
from mini_rag.models import Chunk, RAGQuery
from mini_rag.retrieval import (
    BM25Retriever,
    DenseRetriever,
    HybridRetriever,
    QueryParser,
)


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _chunk(
    chunk_id: str,
    content: str,
    *,
    competitor: str = "Cursor",
    current: bool = True,
    event_type: str = "product_release",
    evidence_level: str = "A",
    version: str | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id=f"doc_{chunk_id}",
        version_id=f"ver_{chunk_id}",
        raw_record_id=f"raw_{chunk_id}",
        raw_path=f"data/raw/{chunk_id}.html",
        chunk_index=0,
        title=content.split(" ", 1)[0],
        content=content,
        heading_path=["Release notes"],
        char_start=0,
        char_end=len(content),
        competitor=competitor,
        source_type="official_changelog",
        evidence_level=evidence_level,
        url=f"https://example.test/{chunk_id}",
        product_version=version,
        publish_time=NOW,
        crawl_time=NOW,
        valid_from=NOW,
        is_current=current,
        event_type=event_type,
        dimension_tags=["agent_context"],
        language="en",
    )


def _indexed_search() -> tuple[
    InMemorySearchBackend,
    EmbeddingService,
    list[Chunk],
]:
    backend = InMemorySearchBackend()
    embeddings = EmbeddingService(HashEmbedding(dimension=64), batch_size=2)
    chunks = [
        _chunk("agent", "Agent context automation and durable side chats"),
        _chunk("price", "Business pricing plan costs 40 dollars per month", event_type="pricing_change"),
        _chunk("old", "Historical agent plan from version 1.0", current=False, version="1.0"),
        _chunk("copilot", "Copilot code completion for Visual Studio", competitor="GitHub Copilot"),
    ]
    report = IndexBuilder(backend, embeddings, "chunks_v1").build(chunks)
    assert report.ok
    backend.swap_alias("chunks_current", "chunks_v1")
    return backend, embeddings, chunks


def test_hash_embedding_is_deterministic_normalized_and_bilingual() -> None:
    provider = HashEmbedding(dimension=64)
    first, second, chinese = provider.embed_documents(
        ["agent context", "agent context", "智能体上下文"]
    )

    assert first == second
    assert first != chinese
    assert len(first) == 64
    assert math.sqrt(sum(value * value for value in first)) == pytest.approx(1.0)
    assert any(chinese)


def test_dense_retriever_revalidates_mapping_after_alias_switch() -> None:
    backend = InMemorySearchBackend()
    first_embeddings = EmbeddingService(HashEmbedding(dimension=64))
    second_embeddings = EmbeddingService(HashEmbedding(dimension=32))
    assert IndexBuilder(backend, first_embeddings, "chunks_v1").build(
        [_chunk("first", "Agent context in the first index")]
    ).ok
    backend.swap_alias("chunks_current", "chunks_v1")
    retriever = DenseRetriever(
        backend,
        "chunks_current",
        embedding_service=first_embeddings,
    )

    assert retriever.search("agent context")
    assert IndexBuilder(backend, second_embeddings, "chunks_v2").build(
        [_chunk("second", "Agent context in the second index")]
    ).ok
    backend.swap_alias("chunks_current", "chunks_v2")

    with pytest.raises(ValueError, match="dense query dimension 64 does not match"):
        retriever.search("agent context")


def test_embedding_service_batches_deduplicates_and_caches() -> None:
    class CountingHash(HashEmbedding):
        calls = 0

        def embed_documents(self, texts):
            self.calls += 1
            return super().embed_documents(texts)

    provider = CountingHash(dimension=32)
    service = EmbeddingService(provider, batch_size=1)
    vectors = service.embed_documents(["same", "same", "different"])
    again = service.embed_documents(["same"])

    assert vectors[0] == vectors[1] == again[0]
    assert provider.calls == 2


def test_mapping_freezes_embedding_compatibility_metadata() -> None:
    mapping = build_index_mapping(128, embedding_model="test/model")

    assert mapping["mappings"]["properties"]["embedding"]["dims"] == 128
    assert mapping["mappings"]["properties"]["embedding"]["similarity"] == "cosine"
    assert mapping["settings"]["index.mapping.exclude_source_vectors"] is False
    assert embedding_metadata(mapping) == ("test/model", 128)


def test_incremental_indexing_skips_unchanged_updates_changed_and_deletes_stale() -> None:
    backend = InMemorySearchBackend()
    embeddings = EmbeddingService(HashEmbedding(dimension=32))
    builder = IndexBuilder(backend, embeddings, "chunks")
    one = _chunk("one", "Agent release one")
    two = _chunk("two", "Pricing release two", event_type="pricing_change")

    first = builder.build([one, two])
    second = builder.build([one, two])
    changed = _chunk("one", "Agent release one with automation")
    third = builder.build([changed], delete_missing=True)
    metadata_changed = changed.model_copy(update={"is_current": False})
    fourth = builder.build([metadata_changed])

    assert first.indexed_count == 2
    assert second.skipped_count == 2
    assert second.indexed_count == 0
    assert third.indexed_count == 1
    assert third.deleted_count == 1
    assert fourth.indexed_count == 1
    assert backend.count("chunks") == 1
    assert backend.get_document("chunks", "one")["content"] == changed.content
    assert backend.get_document("chunks", "one")["is_current"] is False


def test_alias_switch_is_atomic_from_the_reader_view() -> None:
    backend = InMemorySearchBackend()
    embeddings = EmbeddingService(HashEmbedding(dimension=32))
    manager = IndexManager(backend, base_name="chunks", alias="chunks_current")

    first = manager.rebuild([_chunk("one", "first agent release")], embeddings, version="1")
    assert first.ok
    assert backend.count("chunks_current") == 1

    second = manager.rebuild(
        [_chunk("two", "second pricing release", event_type="pricing_change")],
        embeddings,
        version="2",
    )
    assert second.ok
    assert manager.current_index() == "chunks_v2"
    assert backend.get_document("chunks_current", "one") is None
    assert backend.get_document("chunks_current", "two") is not None


def test_bm25_dense_and_hybrid_return_shared_candidates() -> None:
    backend, embeddings, _ = _indexed_search()
    bm25 = BM25Retriever(backend, "chunks_current")
    dense = DenseRetriever(backend, "chunks_current", embedding_service=embeddings)
    hybrid = HybridRetriever(bm25, dense, candidate_k=4)

    lexical = bm25.search("pricing plan", top_k=2)
    semantic = dense.search("agent context automation", top_k=2)
    fused, trace = hybrid.search_with_trace("agent context automation", top_k=3)

    assert lexical[0].chunk_id == "price"
    assert lexical[0].bm25_rank == 1
    assert semantic[0].chunk_id == "agent"
    assert fused[0].chunk_id == "agent"
    assert fused[0].rrf_score is not None
    assert "rrf" in fused[0].retrieval_methods
    assert trace.bm25_candidates > 0
    assert trace.dense_candidates > 0


def test_filters_apply_equally_to_lexical_and_dense_search() -> None:
    backend, embeddings, _ = _indexed_search()
    bm25 = BM25Retriever(backend, "chunks_current")
    dense = DenseRetriever(backend, "chunks_current", embedding_service=embeddings)
    query = RAGQuery(
        question="agent",
        competitor="Cursor",
        current_only=True,
        event_types=["product_release"],
    )

    lexical = bm25.search(query, top_k=10)
    semantic = dense.search(query, top_k=10)

    assert lexical
    assert semantic
    assert all(candidate.chunk.competitor == "Cursor" for candidate in lexical + semantic)
    assert all(candidate.chunk.is_current for candidate in lexical + semantic)
    assert all(candidate.chunk_id != "old" for candidate in lexical + semantic)


def test_query_parser_infers_filters_and_explicit_values_win() -> None:
    parser = QueryParser()
    query = parser.parse(
        "最近3个月 Cursor 当前 pricing v2.1 有什么变化？",
        {"competitor": "GitHub Copilot", "current_only": False, "top_k": 5},
        now=NOW,
    )

    assert query.competitor == "GitHub Copilot"
    assert query.current_only is False
    assert query.product_versions == ["2.1.0"]
    assert query.top_k == 5
    assert query.start_time == NOW - timedelta(days=90)


def test_query_parser_can_disable_implicit_current_filter() -> None:
    parser = QueryParser(current_only_for_current_intent=False)

    query = parser.parse("Cursor 当前 pricing 有什么变化？", now=NOW)

    assert query.current_only is False


def test_explicit_version_and_competitor_filters_use_index_canonical_values() -> None:
    query = RAGQuery(
        question="release",
        competitor="cursor",
        product_versions=["v2.1"],
    )

    assert query.competitor == "Cursor"
    assert query.product_versions == ["2.1.0"]


def test_elasticsearch_filter_dsl_covers_public_query_contract() -> None:
    clauses = build_filter_clauses(
        {
            "competitor": "Cursor",
            "event_types": ["pricing_change"],
            "dimension_tags": ["performance_cost"],
            "current_only": True,
            "start_time": "2026-04-01T00:00:00Z",
        }
    )

    assert {"term": {"competitor": "Cursor"}} in clauses
    assert {"term": {"is_current": True}} in clauses
    assert {"terms": {"event_type": ["pricing_change"]}} in clauses
    assert {"terms": {"dimension_tags": ["performance_cost"]}} in clauses
    assert {
        "bool": {
            "should": [
                {"range": {"publish_time": {"gte": "2026-04-01T00:00:00Z"}}},
                {
                    "bool": {
                        "must_not": [{"exists": {"field": "publish_time"}}],
                        "filter": [
                            {"range": {"valid_from": {"gte": "2026-04-01T00:00:00Z"}}}
                        ],
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    } in clauses


def test_time_filter_uses_valid_from_only_when_publish_time_is_missing() -> None:
    backend = InMemorySearchBackend()
    embeddings = EmbeddingService(HashEmbedding(dimension=32))
    observed = _chunk("observed", "Current pricing snapshot").model_copy(
        update={
            "publish_time": None,
            "valid_from": NOW - timedelta(days=2),
        }
    )
    undated = _chunk("undated", "Snapshot without any usable time").model_copy(
        update={"publish_time": None, "valid_from": None}
    )
    published_before_window = _chunk("published-old", "Old published page").model_copy(
        update={
            "publish_time": NOW - timedelta(days=120),
            "valid_from": NOW - timedelta(days=1),
        }
    )
    report = IndexBuilder(backend, embeddings, "chunks").build(
        [observed, undated, published_before_window]
    )
    assert report.ok

    filters = {
        "start_time": NOW - timedelta(days=90),
        "end_time": NOW,
    }
    hits = backend.search_bm25("chunks", "snapshot page", filters, top_k=10)

    assert [hit.document_id for hit in hits] == ["observed"]
