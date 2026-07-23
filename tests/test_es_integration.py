"""Real Elasticsearch integration tests.

These tests require a running ES instance at ``http://localhost:9200``.
Skipped by default — run with::

    pytest -m es_integration -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from mini_rag.config import MiniRAGSettings
from mini_rag.embedding import EmbeddingService, HashEmbedding
from mini_rag.indexing import (
    ElasticsearchClient,
    IndexBuilder,
    IndexManager,
    SearchBackendError,
    build_index_mapping,
    chunk_to_document,
)
from mini_rag.models import Chunk, RAGQuery

ES_URL = "http://localhost:9200"


# ── helpers ──


def _test_index() -> str:
    return f"test_es_{uuid4().hex[:8]}"


def _chunk(
    chunk_id: str,
    content: str,
    *,
    competitor: str = "Cursor",
    event_type: str = "product_release",
    dimension_tags: list[str] | None = None,
    evidence_level: str = "A",
    version: str | None = None,
    is_current: bool = True,
) -> Chunk:
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    return Chunk(
        chunk_id=chunk_id,
        document_id=f"doc_{chunk_id}",
        version_id=f"ver_{chunk_id}",
        raw_record_id=f"raw_{chunk_id}",
        raw_path=f"data/raw/{chunk_id}.html",
        chunk_index=0,
        title=content.split()[0],
        content=content,
        heading_path=["Release notes"],
        char_start=0,
        char_end=len(content),
        competitor=competitor,
        source_type="official_changelog",
        evidence_level=evidence_level,
        url=f"https://example.test/{chunk_id}",
        product_version=version,
        publish_time=now,
        crawl_time=now,
        valid_from=now,
        is_current=is_current,
        event_type=event_type,
        dimension_tags=dimension_tags or ["agent_context"],
        language="en",
    )


# ── fixtures ──


@pytest.fixture(scope="module")
def es() -> ElasticsearchClient:
    """Shared real ES client (one per module)."""
    client = ElasticsearchClient(ES_URL, verify_certs=False)
    assert client.ping(), f"Elasticsearch not reachable at {ES_URL}"
    return client


@pytest.fixture(scope="module")
def embeddings() -> EmbeddingService:
    """Shared hash embedding service (deterministic, no model download)."""
    return EmbeddingService(HashEmbedding(dimension=64))


@pytest.fixture
def fresh_index(es: ElasticsearchClient):
    """Factory fixture: create a unique temp index with standard mapping.

    Usage::

        idx = fresh_index()        # dimension=64
        idx = fresh_index(dim=128)  # custom dimension
    """
    indices: list[str] = []

    def _create(dim: int = 64) -> str:
        name = _test_index()
        es.create_index(name, build_index_mapping(dim))
        indices.append(name)
        return name

    yield _create
    for name in indices:
        try:
            es.delete_index(name, ignore_missing=True)
        except SearchBackendError:
            pass


# ── auto-cleanup: remove all test_* indices after module completes ──


@pytest.fixture(scope="module", autouse=True)
def _auto_cleanup() -> Generator[None, None, None]:
    """Remove any left-over test indices after the module finishes."""
    yield
    try:
        client = ElasticsearchClient(ES_URL, verify_certs=False, timeout=5)
        for entry in client._request("GET", "/_cat/indices/test_*?format=json"):
            name = entry.get("index")
            if name:
                client.delete_index(name, ignore_missing=True)
    except Exception:
        pass  # best-effort cleanup


# ═══════════════════════════════════════════════════════════════════
# Layer 1: ElasticsearchClient 底层协议验证
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.es_integration
class TestClientProtocol:
    """Verify ElasticsearchClient directly against real ES REST API."""

    def test_ping_and_health(self, es: ElasticsearchClient) -> None:
        """ES 连通性与集群状态."""
        assert es.ping() is True
        health = es.health()
        assert health.get("status") in ("green", "yellow")
        assert health.get("cluster_name")

    def test_index_lifecycle(self, es: ElasticsearchClient) -> None:
        """create → exists → get_mapping → delete 全生命周期."""
        name = _test_index()
        try:
            assert es.index_exists(name) is False

            es.create_index(name, build_index_mapping(embedding_dimension=64))
            assert es.index_exists(name) is True

            mapping = es.get_mapping(name)
            props = mapping[name]["mappings"]["properties"]
            assert "embedding" in props
            assert props["embedding"]["dims"] == 64
            assert props["embedding"]["similarity"] == "cosine"
            assert props["chunk_id"]["type"] == "keyword"
        finally:
            es.delete_index(name, ignore_missing=True)

    def test_bulk_index_and_search_bm25(
        self, es: ElasticsearchClient, fresh_index: callable
    ) -> None:
        """bulk_index 写入多文档 → search_bm25 返回 BM25 相关性排序."""
        idx = fresh_index(dim=64)
        docs = [
            {
                "chunk_id": "bm25_a1",
                "title": "Agent",
                "content": "Agent side chats and durable context automation",
                "competitor": "Cursor",
                "source_type": "official_changelog",
                "url": "https://example.test/bm25_a1",
                "publish_time": "2026-07-14T08:00:00Z",
                "event_type": "product_release",
            },
            {
                "chunk_id": "bm25_b1",
                "title": "Pricing",
                "content": "Business pricing plan costs 40 dollars per month",
                "competitor": "Cursor",
                "source_type": "official_changelog",
                "url": "https://example.test/bm25_b1",
                "publish_time": "2026-07-14T08:00:00Z",
                "event_type": "pricing_change",
            },
            {
                "chunk_id": "bm25_c1",
                "title": "Security",
                "content": "Enterprise security audit logs get SOC 2 compliance",
                "competitor": "Cursor",
                "source_type": "official_changelog",
                "url": "https://example.test/bm25_c1",
                "publish_time": "2026-07-14T08:00:00Z",
                "event_type": "product_release",
            },
        ]
        result = es.bulk_index(idx, docs, id_field="chunk_id", refresh=True)
        assert result.ok
        assert result.indexed == 3
        assert result.failed == 0

        # BM25 找到定价文档
        hits = es.search_bm25(idx, "pricing plan", top_k=3)
        assert len(hits) >= 1
        assert hits[0].document_id == "bm25_b1"
        assert hits[0].score > 0.0

        # 过滤器排除定价文档
        filtered = es.search_bm25(
            idx,
            "pricing",
            filters={"event_types": ["product_release"]},
            top_k=3,
        )
        assert all(
            h.source.get("event_type") == "product_release" for h in filtered
        )

    def test_search_dense(
        self,
        es: ElasticsearchClient,
        fresh_index: callable,
        embeddings: EmbeddingService,
    ) -> None:
        """Hash 向量写入 → knn 向量检索返回最近邻."""
        idx = fresh_index(dim=64)
        chunks = [
            _chunk("dense_a", "Agent repository scanning and auto-indexing"),
            _chunk("dense_b", "Pricing calculator for team plan"),
        ]
        texts = [c.content for c in chunks]
        vecs = embeddings.embed_documents(texts)
        docs = []
        for chunk, vec in zip(chunks, vecs):
            d = chunk_to_document(chunk)
            d["embedding"] = list(vec)
            d["embedding_model"] = "hash"
            d["embedding_dimension"] = 64
            docs.append(d)

        result = es.bulk_index(idx, docs, id_field="chunk_id", refresh=True)
        assert result.ok
        assert result.indexed == 2

        # 搜索向量接近 "repository" 的 chunk
        q_vec = embeddings.embed_query("repository scanning")
        hits = es.search_dense(idx, q_vec, filters={"competitors": ["Cursor"]}, top_k=3)
        assert len(hits) >= 1
        assert hits[0].document_id == "dense_a"
        assert hits[0].score > 0.0

    def test_alias_swap(self, es: ElasticsearchClient) -> None:
        """swap_alias 原子切换 → resolve_alias 返回正确物理索引."""
        base = _test_index()
        alias = _test_index() + "_read"
        idx_a = f"{base}_v1"
        idx_b = f"{base}_v2"
        mapping = build_index_mapping(embedding_dimension=64)
        try:
            es.create_index(idx_a, mapping)
            es.create_index(idx_b, mapping)

            # 别名不存在时返回 None
            assert es.resolve_alias(alias) is None

            # 首次 swap：添加别名
            es.swap_alias(alias, idx_a)
            assert es.resolve_alias(alias) == idx_a

            # 再次 swap：原子切换到 idx_b
            es.swap_alias(alias, idx_b)
            resolved = es.resolve_alias(alias)
            assert resolved == idx_b
            assert resolved != idx_a
        finally:
            es.delete_index(idx_a, ignore_missing=True)
            es.delete_index(idx_b, ignore_missing=True)

    def test_error_recovery(self, es: ElasticsearchClient) -> None:
        """不存在的索引 / 操作失败时抛 SearchBackendError."""
        missing = _test_index() + "_nonexistent"

        # BM25 在不存在的索引上
        with pytest.raises(SearchBackendError):
            es.search_bm25(missing, "test")

        # delete_index 非静默模式
        with pytest.raises(SearchBackendError):
            es.delete_index(missing, ignore_missing=False)

        # count 在不存在的索引上
        with pytest.raises(SearchBackendError):
            es.count(missing)


# ═══════════════════════════════════════════════════════════════════
# Layer 2: MiniRAGService 完整链路集成验证
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.es_integration
class TestServicePipeline:
    """Verify MiniRAGService end-to-end against real ES."""

    def test_full_pipeline(
        self,
        es: ElasticsearchClient,
        embeddings: EmbeddingService,
        tmp_path: callable,
    ) -> None:
        """build_index（IndexManager.rebuild）→ query → evidence 全链路."""
        alias = _test_index() + "_read"
        settings = MiniRAGSettings.model_validate({
            "project_root": str(tmp_path),
            "data": {"trace_path": "traces.jsonl"},
            "embedding": {"provider": "hash", "dimension": 64},
            "elasticsearch": {"url": ES_URL, "read_alias": alias},
            "retrieval": {"default_top_k": 5, "maximum_top_k": 20},
        })
        from mini_rag.api import create_service

        chunks = [
            _chunk("pipe_a", "Agent now supports durable side chats and repository context"),
            _chunk("pipe_b", "Enterprise pricing updated with team tier at 40 per month"),
        ]
        manager = IndexManager(
            es, base_name=_test_index(), alias=alias, shards=1, replicas=0
        )
        report = manager.rebuild(chunks, embeddings, version="1")
        assert report.ok, f"rebuild failed: {report.errors}"

        service = create_service(settings, backend=es)
        response = service.query(RAGQuery(question="Agent side chat", top_k=3))

        assert response.evidence, "query returned no evidence"
        assert response.evidence[0].competitor == "Cursor"
        assert "durable" in response.evidence[0].content
        assert response.retrieval_trace is not None
        assert response.retrieval_trace.retrieval_config["reranker_backend"] == "term_overlap"

    def test_metadata_filters(
        self,
        es: ElasticsearchClient,
        embeddings: EmbeddingService,
        tmp_path: callable,
    ) -> None:
        """竞品 / 事件类型 / 维度标签过滤在真实 ES 上生效."""
        alias = _test_index() + "_read"
        settings = MiniRAGSettings.model_validate({
            "project_root": str(tmp_path),
            "data": {"trace_path": "traces.jsonl"},
            "embedding": {"provider": "hash", "dimension": 64},
            "elasticsearch": {"url": ES_URL, "read_alias": alias},
            "retrieval": {"default_top_k": 10, "maximum_top_k": 20},
        })
        from mini_rag.api import create_service

        chunks = [
            _chunk("f1", "Agent side chat with durable memory", competitor="Cursor"),
            _chunk(
                "f2",
                "Copilot code completion with multi-model",
                competitor="GitHub Copilot",
                event_type="pricing_change",
            ),
            _chunk(
                "f3",
                "Agent pricing plan now includes team features",
                competitor="Cursor",
                event_type="pricing_change",
            ),
        ]
        manager = IndexManager(
            es, base_name=_test_index(), alias=alias, shards=1, replicas=0
        )
        report = manager.rebuild(chunks, embeddings, version="1")
        assert report.ok

        service = create_service(settings, backend=es)

        # 按竞品过滤
        r = service.query(RAGQuery(question="agent", competitor="Cursor", top_k=10))
        assert all(e.competitor == "Cursor" for e in r.evidence)
        assert len(r.evidence) == 2

        # 按事件类型过滤
        r2 = service.query(
            RAGQuery(question="pricing", event_types=["pricing_change"], top_k=10)
        )
        assert len(r2.evidence) == 2
        assert all(e.event_type == "pricing_change" for e in r2.evidence)

    def test_incremental_build(
        self,
        es: ElasticsearchClient,
        embeddings: EmbeddingService,
    ) -> None:
        """增量构建幂等性."""
        idx = _test_index()
        try:
            es.create_index(idx, build_index_mapping(embedding_dimension=64))
            builder = IndexBuilder(es, embeddings, idx)

            chunks = [
                _chunk("inc_a", "Agent initial release feature set"),
                _chunk("inc_b", "Pricing initial cost structure"),
            ]
            r1 = builder.build(chunks, recreate=False, refresh=True)
            assert r1.indexed_count == 2, f"first build indexed {r1.indexed_count}"

            # 相同内容再次构建 → 全部跳过（幂等）
            r2 = builder.build(chunks, recreate=False, incremental=True, refresh=True)
            assert r2.skipped_count >= 2, (
                f"expected all skipped, got indexed={r2.indexed_count} skipped={r2.skipped_count}"
            )

            # 部分内容变更 → 只索引变更项
            changed = [_chunk("inc_a", "Agent updated with new automation features")]
            r3 = builder.build(changed, recreate=False, incremental=True, refresh=True)
            assert r3.indexed_count >= 1, f"change not detected: {r3}"
        finally:
            es.delete_index(idx, ignore_missing=True)
