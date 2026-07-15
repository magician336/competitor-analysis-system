from __future__ import annotations

import pytest

from mini_rag.api import create_service
from mini_rag.chunking import ChunkingDispatcher
from mini_rag.config import MiniRAGSettings
from mini_rag.embedding import EmbeddingService, HashEmbedding
from mini_rag.evaluation import ABLATION_SPECS
from mini_rag.indexing import InMemorySearchBackend, IndexManager, SearchBackendError
from mini_rag.models import EvaluationCase, RAGQuery
from schemas.document import StructuredDocument


def _document(title: str, content: str, *, source_type: str = "official_page") -> StructuredDocument:
    return StructuredDocument(
        raw_record_id=f"raw_{title}",
        raw_path=f"data/raw/{title}.html",
        competitor="Cursor",
        title=title,
        content=content,
        source_type=source_type,
        evidence_level="A",
        url=f"https://cursor.example/{title}",
        publish_time="2026-07-10T08:00:00Z",
        crawl_time="2026-07-14T08:00:00Z",
        event_type="product_release",
        dimension_tags=["agent_context"],
    )


def test_complete_memory_service_build_query_trace_and_validation(tmp_path) -> None:
    documents_path = tmp_path / "data" / "cleaned" / "documents.jsonl"
    documents_path.parent.mkdir(parents=True)
    documents = [
        _document(
            "Agent update",
            "# Agent\n\nCursor Agent now reads repository context and edits multiple files.",
            source_type="official_changelog",
        ),
        _document("Security", "# Security\n\nEnterprise audit logs are available."),
    ]
    documents_path.write_text(
        "\n".join(item.model_dump_json() for item in documents) + "\n",
        encoding="utf-8",
    )
    settings = MiniRAGSettings.model_validate(
        {
            "project_root": tmp_path,
            "data": {
                "documents_path": "data/cleaned/documents.jsonl",
                "trace_path": "data/runtime/traces.jsonl",
            },
            "embedding": {"provider": "hash", "dimension": 64},
            "retrieval": {
                "bm25_candidates": 5,
                "dense_candidates": 5,
                "fused_candidates": 5,
                "rerank_candidates": 5,
                "default_top_k": 2,
                "maximum_top_k": 10,
            },
        }
    )
    backend = InMemorySearchBackend()
    service = create_service(settings, backend=backend)

    build = service.build_index(rebuild=True)
    response = service.query(RAGQuery(question="Cursor 当前 Agent 有哪些更新", top_k=2))

    assert build["documents"] == 2
    assert build["report"]["indexed_count"] >= 2
    assert response.parsed_filters["competitor"] == "Cursor"
    assert response.parsed_filters["current_only"] is True
    assert response.evidence
    assert "repository context" in response.evidence[0].content
    assert response.retrieval_trace.retrieval_config["reranker_backend"] == "term_overlap"
    assert response.retrieval_trace.retrieval_config["reranker_error"] is None
    assert service.get_trace(response.query_id) is not None
    stored = service.get_evidence(response.evidence[0].chunk_id)
    assert stored is not None
    validation = service.validate_citations(
        [
            {
                "chunk_id": stored.chunk_id,
                "url": stored.url,
                "quote": "repository context",
            }
        ],
        query_id=response.query_id,
    )
    assert validation.valid is True


def test_service_honours_limits_and_runs_all_six_ablations(tmp_path) -> None:
    documents_path = tmp_path / "documents.jsonl"
    documents_path.write_text(
        _document(
            "Agent update",
            "# Agent\n\nCursor Agent reads repository context.",
            source_type="official_changelog",
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    settings = MiniRAGSettings.model_validate(
        {
            "project_root": tmp_path,
            "data": {
                "documents_path": "documents.jsonl",
                "trace_path": "traces.jsonl",
            },
            "embedding": {"provider": "hash", "dimension": 32},
            "reranker": {"provider": "cross_encoder", "model": "fake/test"},
            "retrieval": {
                "bm25_candidates": 4,
                "dense_candidates": 4,
                "fused_candidates": 4,
                "rerank_candidates": 4,
                "default_top_k": 1,
                "maximum_top_k": 10,
            },
        }
    )
    service = create_service(settings, backend=InMemorySearchBackend())
    class FakeCrossEncoder:
        def predict(self, pairs, batch_size=8):
            return [1.0 for _ in pairs]

    service.ranking_pipeline.reranker.model = FakeCrossEncoder()
    service.ranking_pipeline.reranker._load_attempted = True
    service.build_index()

    lexical = service.query_with_ablation("Cursor Agent", ABLATION_SPECS[0])
    dense = service.query_with_ablation("Cursor Agent", ABLATION_SPECS[1])
    results = service.evaluate_ablations(
        [EvaluationCase(case_id="agent", question="Cursor Agent")]
    )

    assert lexical.retrieval_trace.bm25_candidates > 0
    assert lexical.retrieval_trace.dense_candidates == 0
    assert dense.retrieval_trace.bm25_candidates == 0
    assert dense.retrieval_trace.dense_candidates > 0
    assert lexical.retrieval_trace.retrieval_config["ablation"]["group"] == "A"
    assert list(results) == ["A", "B", "C", "D", "E", "F"]
    assert results["F"].config["embedding_dimension"] == 32
    assert len(service.query("Cursor Agent").evidence) == 1
    with pytest.raises(ValueError, match="configured maximum"):
        service.query(RAGQuery(question="Cursor Agent", top_k=11))


def test_incremental_build_never_creates_a_physical_index_named_like_alias(tmp_path) -> None:
    documents_path = tmp_path / "documents.jsonl"
    documents_path.write_text(
        _document("Agent", "Agent repository context.").model_dump_json() + "\n",
        encoding="utf-8",
    )
    settings = MiniRAGSettings.model_validate(
        {
            "project_root": tmp_path,
            "data": {"documents_path": "documents.jsonl"},
            "embedding": {"provider": "hash", "dimension": 16},
        }
    )
    backend = InMemorySearchBackend()
    service = create_service(settings, backend=backend)

    with pytest.raises(SearchBackendError, match="full index rebuild"):
        service.build_index(rebuild=False)

    assert backend.index_exists(settings.elasticsearch.read_alias) is False


def test_service_health_reports_embedding_compatibility(tmp_path) -> None:
    settings = MiniRAGSettings.model_validate(
        {
            "project_root": tmp_path,
            "data": {"documents_path": "documents.jsonl"},
            "embedding": {"provider": "hash", "dimension": 32},
        }
    )
    backend = InMemorySearchBackend()
    service = create_service(settings, backend=backend)
    incompatible = EmbeddingService(HashEmbedding(dimension=64))
    chunks = ChunkingDispatcher().chunk(
        _document("Agent", "Agent context.")
    )
    assert IndexManager(
        backend,
        base_name="chunks",
        alias=settings.elasticsearch.read_alias,
    ).rebuild(
        chunks,
        incompatible,
        version="mismatch",
    ).ok

    health = service.health()

    assert health["status"] == "degraded"
    assert health["embedding_compatible"] is False
    assert health["embedding_dimension"] == 32
    assert health["index_embedding_dimension"] == 64
