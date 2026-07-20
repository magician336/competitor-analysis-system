from __future__ import annotations

from fastapi.testclient import TestClient

from agents import BenchmarkAgent, CompareAgent, PriceAgent
from backend.main import create_app
from backend.services.agent_service import AgentService, get_agent_service
from mini_rag.models import Evidence, RAGResponse, RetrievalTrace
from schemas.document import DimensionTag
from schemas.intelligence_card import AgentAnalysisRequest


class FakeRAGService:
    def query(self, request):
        return RAGResponse(
            query=request.question,
            parsed_filters=request.filters(),
            evidence=[
                Evidence(
                    chunk_id="chunk_price_1",
                    document_id="doc_1",
                    version_id="ver_1",
                    content="The student plan includes limited premium requests.",
                    title="Pricing update",
                    url="https://example.test/pricing",
                    char_start=0,
                    char_end=52,
                    competitor=request.competitor or "Cursor",
                    source_type="pricing",
                    evidence_level="A",
                    event_type="pricing_change",
                    dimension_tags=["performance_cost", "education_fit"],
                )
            ],
            retrieval_trace=RetrievalTrace(returned_candidates=1, latency_ms=1.2),
        )


def test_price_agent_generates_evidence_backed_card() -> None:
    result = PriceAgent(FakeRAGService()).run(
        AgentAnalysisRequest(competitor="Cursor", top_k=3)
    )

    assert result.evidence_count == 1
    card = result.cards[0]
    assert card.event_type == "pricing_change"
    assert card.evidence[0].chunk_id == "chunk_price_1"
    assert card.confidence_score > 0.5
    assert card.priority_score >= 40


def test_compare_agent_builds_snapshot_from_cards() -> None:
    result = PriceAgent(FakeRAGService()).run(
        AgentAnalysisRequest(competitor="Cursor", top_k=3)
    )
    snapshot = CompareAgent().build_snapshot("Cursor", result.cards)

    assert snapshot.competitor == "Cursor"
    assert snapshot.scores[DimensionTag.PERFORMANCE_COST] > 20
    assert snapshot.evidence_count[DimensionTag.PERFORMANCE_COST] == 1


def test_agent_api_uses_service_dependency_override() -> None:
    app = create_app()
    service = AgentService(FakeRAGService())
    app.dependency_overrides[get_agent_service] = lambda: service
    client = TestClient(app)

    response = client.post("/api/agent/price", json={"competitor": "Cursor", "top_k": 3})
    assert response.status_code == 200
    assert response.json()["cards"][0]["evidence"][0]["chunk_id"] == "chunk_price_1"

    snapshot = client.post("/api/agent/compare", json={"competitor": "Cursor"})
    assert snapshot.status_code == 200
    assert snapshot.json()["scores"]["performance_cost"] > 20


def test_benchmark_tasks_and_sample_runs_load() -> None:
    agent = BenchmarkAgent()
    tasks = agent.load_tasks()
    rows = agent.compare()

    assert len(tasks) == 16
    assert {task.task_id for task in tasks} >= {"bench_001", "bench_016"}
    assert any(row.competitor == "Cursor" for row in rows)
