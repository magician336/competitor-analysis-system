from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services.rag_service import get_rag_service
from mini_rag.models import Evidence, RAGResponse, RetrievalTrace


class FakeService:
    def query(self, request):
        return RAGResponse(
            query=request.question,
            parsed_filters=request.filters(),
            evidence=[],
            retrieval_trace=RetrievalTrace(latency_ms=1.0),
        )

    def health(self):
        return {
            "status": "ready",
            "index": "chunks_current",
            "physical_index": "chunks_v1",
            "indexed_chunks": 2,
            "embedding_model": "hash-test",
            "embedding_dimension": 32,
            "backend": {"status": "green"},
        }

    def get_trace(self, query_id):
        return {"query_id": query_id, "query": "test"} if query_id == "q1" else None

    def get_evidence(self, chunk_id):
        if chunk_id != "chunk_1":
            return None
        return Evidence(
            chunk_id="chunk_1",
            document_id="doc_1",
            version_id="ver_1",
            content="Agent supports repository context.",
            title="Release",
            url="https://example.test/release",
            char_start=0,
            char_end=34,
            competitor="Cursor",
            source_type="official_changelog",
            evidence_level="A",
        )


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_rag_service] = lambda: FakeService()
    return TestClient(app)


def test_query_endpoint_uses_public_contract() -> None:
    response = _client().post(
        "/api/rag/query",
        json={"question": "Cursor 当前有哪些 Agent 更新", "current_only": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Cursor 当前有哪些 Agent 更新"
    assert body["parsed_filters"] == {"current_only": True}
    assert body["query_id"].startswith("qry_")


def test_health_trace_and_evidence_endpoints() -> None:
    client = _client()
    health = client.get("/health")
    assert health.json() == {"status": "ok"}
    assert health.headers["content-type"] == "application/json; charset=utf-8"
    assert client.get("/ready").json()["status"] == "ready"
    assert client.get("/api/rag/index/status").json()["indexed_chunks"] == 2
    assert client.get("/api/rag/trace/q1").status_code == 200
    assert client.get("/api/rag/trace/missing").status_code == 404
    evidence = client.get("/api/rag/evidence/chunk_1")
    assert evidence.status_code == 200
    assert evidence.json()["evidence_level"] == "A"
