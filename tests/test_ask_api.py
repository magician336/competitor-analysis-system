from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services.ask_service import AskService
from backend.services.rag_service import get_rag_service
from mini_rag.models import Evidence, RAGResponse, RetrievalTrace
from schemas.ask import AskRequest


def _evidence() -> Evidence:
    return Evidence(
        chunk_id="chunk_agent_update",
        document_id="doc_agent_update",
        version_id="ver_agent_update",
        content="Agent mode supports repository-wide context.",
        quote="Agent mode supports repository-wide context.",
        title="Agent update",
        url="https://example.test/agent-update",
        char_start=0,
        char_end=44,
        competitor="Cursor",
        source_type="official_changelog",
        evidence_level="A",
    )


class AskRagService:
    def __init__(self, *, evidence: bool = True) -> None:
        self.include_evidence = evidence

    def query(self, request):
        return RAGResponse(
            query=request.question,
            parsed_filters=request.filters(),
            evidence=[_evidence()] if self.include_evidence else [],
            retrieval_trace=RetrievalTrace(latency_ms=1.0),
        )


def test_ask_api_returns_rules_fallback_with_traceable_references(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    app = create_app()
    app.dependency_overrides[get_rag_service] = lambda: AskRagService()
    response = TestClient(app).post(
        "/api/ask",
        json={"question": "最近有哪些 Agent 更新？", "analysis_target": "Cursor"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer_mode"] == "rules_fallback"
    assert "[1]" in body["answer"]
    assert body["references"][0]["chunk_id"] == "chunk_agent_update"
    assert body["query_id"].startswith("qry_")


def test_ask_service_accepts_only_valid_model_citations() -> None:
    valid = AskService(
        AskRagService(),
        answerer=lambda _question, _evidence: "近期加入了项目级上下文能力 [1]。",
    ).ask(AskRequest(question="有什么更新？"))
    invalid = AskService(
        AskRagService(),
        answerer=lambda _question, _evidence: "这是一个没有来源或使用 [9] 的回答。",
    ).ask(AskRequest(question="有什么更新？"))

    assert valid.answer_mode == "hybrid"
    assert invalid.answer_mode == "rules_fallback"
    assert "[1]" in invalid.answer


def test_ask_service_normalizes_model_markdown_to_plain_text() -> None:
    answer = AskService(
        AskRagService(),
        answerer=lambda _question, _evidence: (
            "## 结论\n\n- **项目级上下文**已经得到支持 [1]。\n"
            "- `Agent mode` 可用于仓库分析 [1]。"
        ),
    ).ask(AskRequest(question="有什么更新？"))

    assert answer.answer_mode == "hybrid"
    assert "[1]" in answer.answer
    assert not any(marker in answer.answer for marker in ("##", "**", "`", "- "))
    assert "项目级上下文" in answer.answer


def test_ask_api_is_explicit_when_no_evidence_exists() -> None:
    app = create_app()
    app.dependency_overrides[get_rag_service] = lambda: AskRagService(evidence=False)
    body = TestClient(app).post("/api/ask", json={"question": "未知问题"}).json()
    assert body["references"] == []
    assert "没有找到足够证据" in body["answer"]


def test_rules_fallback_removes_markdown_from_retrieved_excerpt() -> None:
    class MarkdownRagService(AskRagService):
        def query(self, request):
            response = super().query(request)
            response.evidence[0] = response.evidence[0].model_copy(
                update={"quote": "## Reliability\n- Agent mode is more stable."}
            )
            return response

    answer = AskService(
        MarkdownRagService(),
        answerer=lambda _question, _evidence: "没有合法引用",
    ).ask(AskRequest(question="稳定性如何？"))

    assert answer.answer_mode == "rules_fallback"
    assert "[1]" in answer.answer
    assert not any(marker in answer.answer for marker in ("##", "- "))
