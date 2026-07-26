from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api_repository import FormalApiRepository, get_formal_api_repository
from backend.auth import AuthRepository, get_auth_repository
from backend.database import Database
from backend.history_repository import HistoryRepository, get_history_repository
from backend.main import create_app
from backend.models import Base, BriefingRecord, UserSessionRecord, WorkflowRecord
from backend.seed_demo_users import seed_demo_users
from backend.services.rag_service import get_rag_service
from backend.services.workflow_service import WorkflowService, get_workflow_service
from backend.workflow_repository import (
    AsyncWorkflowRepository,
    WorkflowNotFoundError,
)
from mini_rag.models import Evidence, RAGQuery, RAGResponse, RetrievalTrace
from schemas.auth import RegisterRequest
from schemas.orchestration import (
    MultiAgentAnalysisResult,
    WorkflowExecutionStatus,
)
from schemas.workflow import WorkflowSubmitRequest


class FakeRagService:
    def __init__(self) -> None:
        self.traces: dict[str, RAGResponse] = {}

    def query(self, request: RAGQuery) -> RAGResponse:
        response = RAGResponse(
            query_id=f"qry_{request.question[-6:]}",
            query=request.question,
            parsed_filters=request.filters(),
            evidence=[
                Evidence(
                    chunk_id="chunk_auth_1",
                    document_id="doc_auth_1",
                    version_id="ver_auth_1",
                    content="Cursor Agent supports repository context.",
                    title="Cursor release",
                    url="https://example.test/cursor",
                    char_start=0,
                    char_end=41,
                    competitor="Cursor",
                    source_type="official_changelog",
                    evidence_level="A",
                )
            ],
            retrieval_trace=RetrievalTrace(
                returned_candidates=1,
                latency_ms=12.5,
            ),
        )
        self.traces[response.query_id] = response
        return response

    def get_trace(self, query_id: str) -> RAGResponse | None:
        return self.traces.get(query_id)


@pytest.fixture
def auth_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("CODERADAR_AUTH_ENABLED", "true")
    monkeypatch.setenv("CODERADAR_API_KEY", "machine-key")
    monkeypatch.setenv("CODERADAR_COOKIE_SECURE", "false")
    database = Database(f"sqlite:///{(tmp_path / 'auth.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    auth = AuthRepository(database.session_factory)
    history = HistoryRepository(database.session_factory)
    workflow = AsyncWorkflowRepository(database.session_factory)
    app = create_app()
    app.dependency_overrides[get_auth_repository] = lambda: auth
    app.dependency_overrides[get_history_repository] = lambda: history
    fake_rag = FakeRagService()
    app.dependency_overrides[get_rag_service] = lambda: fake_rag
    app.dependency_overrides[get_workflow_service] = lambda: WorkflowService(
        workflow
    )
    app.dependency_overrides[get_formal_api_repository] = lambda: FormalApiRepository(
        database.session_factory
    )
    try:
        yield database, auth, history, app
    finally:
        database.dispose()


def _register(client: TestClient, username: str, password: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201
    return response.json()


def test_register_login_logout_and_cookie_contract(auth_stack) -> None:
    _database, _auth, _history, app = auth_stack
    client = TestClient(app)
    created = _register(client, "alpha_user", "AlphaPass2026")
    assert created["username"] == "alpha_user"
    cookie = client.cookies.get("coderadar_session")
    assert cookie
    set_cookie = client.post(
        "/api/auth/login",
        json={"username": "ALPHA_USER", "password": "AlphaPass2026"},
    ).headers["set-cookie"].casefold()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert client.get("/api/auth/me").json()["user_id"] == created["user_id"]

    duplicate = client.post(
        "/api/auth/register",
        json={"username": "Alpha_User", "password": "Different2026"},
    )
    assert duplicate.status_code == 409
    invalid = client.post(
        "/api/auth/login",
        json={"username": "alpha_user", "password": "WrongPass2026"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "用户名或密码错误"

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_ask_and_evidence_history_is_user_isolated(auth_stack, monkeypatch) -> None:
    _database, _auth, _history, app = auth_stack
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    alpha = TestClient(app)
    beta = TestClient(app)
    _register(alpha, "history_alpha", "HistoryAlpha2026")
    _register(beta, "history_beta", "HistoryBeta2026")

    ask = alpha.post(
        "/api/ask",
        json={"question": "Cursor Agent 最近有什么变化？", "top_k": 8},
    )
    assert ask.status_code == 200
    ask_id = ask.json()["ask_id"]
    ask_history = alpha.get("/api/ask/history").json()
    assert ask_history["total"] == 1
    assert ask_history["items"][0]["ask_id"] == ask_id
    assert beta.get("/api/ask/history").json()["total"] == 0
    assert beta.get(f"/api/ask/history/{ask_id}").status_code == 404

    evidence = alpha.post(
        "/api/rag/query",
        json={"question": "检索 Cursor 的上下文能力", "top_k": 10},
    )
    assert evidence.status_code == 200
    query_id = evidence.json()["query_id"]
    evidence_history = alpha.get("/api/rag/history").json()
    assert evidence_history["total"] == 1
    assert evidence_history["items"][0]["result_count"] == 1
    assert beta.get("/api/rag/history").json()["total"] == 0
    assert beta.get(f"/api/rag/history/{query_id}").status_code == 404

    machine = TestClient(app)
    machine_response = machine.post(
        "/api/ask",
        headers={"X-API-Key": "machine-key"},
        json={"question": "API Key 调用不进入历史", "top_k": 8},
    )
    assert machine_response.status_code == 200
    assert alpha.get("/api/ask/history").json()["total"] == 1
    assert beta.get("/api/ask/history").json()["total"] == 0


def test_workflow_idempotency_is_scoped_to_user(auth_stack) -> None:
    database, auth, _history, _app = auth_stack
    first = auth.register(
        RegisterRequest(username="workflow_one", password="WorkflowOne2026")
    )
    second = auth.register(
        RegisterRequest(username="workflow_two", password="WorkflowTwo2026")
    )
    repository = AsyncWorkflowRepository(database.session_factory)
    request = WorkflowSubmitRequest(
        competitor="Cursor", correlation_id="same-user-payload"
    ).to_analysis_request()

    first_submission = repository.submit(request, user_id=first.user_id)
    first_duplicate = repository.submit(request, user_id=first.user_id)
    second_submission = repository.submit(request, user_id=second.user_id)
    assert first_duplicate.workflow_id == first_submission.workflow_id
    assert first_duplicate.deduplicated is True
    assert second_submission.workflow_id != first_submission.workflow_id
    assert second_submission.deduplicated is False
    assert repository.get(
        first_submission.workflow_id, user_id=first.user_id
    ).workflow_id == first_submission.workflow_id
    with pytest.raises(WorkflowNotFoundError):
        repository.get(first_submission.workflow_id, user_id=second.user_id)

    first_claim = repository.claim_next(
        "scope-worker", lease_seconds=30, timeout_seconds=30
    )
    assert first_claim is not None
    repository.finalize_result(
        MultiAgentAnalysisResult(
            workflow_id=first_claim.workflow_id,
            request_fingerprint=first_claim.request_fingerprint,
            request=first_claim.request,
            status=WorkflowExecutionStatus.SUCCESS,
            duration_ms=0,
        ),
        attempt=first_claim.attempt,
    )
    second_claim = repository.claim_next(
        "scope-worker", lease_seconds=30, timeout_seconds=30
    )
    assert second_claim is not None
    repository.finalize_result(
        MultiAgentAnalysisResult(
            workflow_id=second_claim.workflow_id,
            request_fingerprint=second_claim.request_fingerprint,
            request=second_claim.request,
            status=WorkflowExecutionStatus.SUCCESS,
            duration_ms=0,
        ),
        attempt=second_claim.attempt,
    )
    completed_duplicate = repository.submit(request, user_id=first.user_id)
    assert completed_duplicate.workflow_id == first_submission.workflow_id
    assert completed_duplicate.deduplicated is True


def test_login_and_registration_are_rate_limited(auth_stack, monkeypatch) -> None:
    _database, _auth, _history, _app = auth_stack
    monkeypatch.setenv("CODERADAR_AUTH_RATE_PER_MINUTE", "2")
    app = create_app()
    app.dependency_overrides[get_auth_repository] = _app.dependency_overrides[
        get_auth_repository
    ]
    client = TestClient(app)
    for _ in range(2):
        response = client.post(
            "/api/auth/login",
            json={"username": "missing_user", "password": "WrongPass2026"},
        )
        assert response.status_code == 401
    limited = client.post(
        "/api/auth/login",
        json={"username": "missing_user", "password": "WrongPass2026"},
    )
    assert limited.status_code == 429
    assert limited.headers["retry-after"]


def test_session_expiry_and_cors_preflight(auth_stack) -> None:
    database, _auth, _history, app = auth_stack
    client = TestClient(app)
    _register(client, "expiring_user", "ExpiringUser2026")
    with database.session_factory.begin() as session:
        record = session.query(UserSessionRecord).one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert client.get("/api/auth/me").status_code == 401

    preflight = client.options(
        "/api/ask",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == (
        "http://localhost:5173"
    )
    assert "POST" in preflight.headers["access-control-allow-methods"]


def test_api_key_is_limited_to_machine_workflows_and_traces(
    auth_stack, monkeypatch
) -> None:
    database, _auth, _history, app = auth_stack
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    user = TestClient(app)
    other = TestClient(app)
    machine = TestClient(app, headers={"X-API-Key": "machine-key"})
    _register(user, "owned_workflow", "OwnedWorkflow2026")
    _register(other, "other_workflow", "OtherWorkflow2026")

    owned = user.post(
        "/api/workflows",
        json={"competitor": "Cursor", "correlation_id": "owned"},
    )
    assert owned.status_code == 202
    owned_id = owned.json()["workflow_id"]
    assert machine.get("/api/workflows").json()["total"] == 0
    assert machine.get(f"/api/workflows/{owned_id}").status_code == 404
    assert machine.post(f"/api/workflows/{owned_id}/cancel").status_code == 404

    with database.session_factory.begin() as session:
        session.add(
            BriefingRecord(
                briefing_id="briefing_owned",
                competitor="Cursor",
                workflow_id=owned_id,
                markdown="# Owned",
                created_at=datetime.now(timezone.utc),
                payload={},
            )
        )
    assert user.get("/api/briefings/briefing_owned").status_code == 200
    assert machine.get("/api/briefings/briefing_owned").status_code == 404

    machine_workflow = machine.post(
        "/api/workflows",
        json={"competitor": "Cursor", "correlation_id": "machine"},
    )
    assert machine_workflow.status_code == 202
    assert machine.get("/api/workflows").json()["total"] == 1
    assert user.get("/api/workflows").json()["total"] == 1

    user_query = user.post(
        "/api/rag/query",
        json={"question": "用户专属检索轨迹", "top_k": 10},
    ).json()["query_id"]
    assert user.get(f"/api/rag/trace/{user_query}").status_code == 200
    assert other.get(f"/api/rag/trace/{user_query}").status_code == 404
    assert machine.get(f"/api/rag/trace/{user_query}").status_code == 404

    machine_query = machine.post(
        "/api/rag/query",
        json={"question": "机器调用检索轨迹", "top_k": 10},
    ).json()["query_id"]
    assert machine.get(f"/api/rag/trace/{machine_query}").status_code == 200


def test_seed_recovers_legacy_workflows_when_demo_users_already_exist(
    tmp_path,
) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'seed.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    auth = AuthRepository(database.session_factory)
    first, _ = auth.create_user_if_missing(
        username="coderadar_test1", password="CodeRadar@2026-1"
    )
    auth.create_user_if_missing(
        username="coderadar_test2", password="CodeRadar@2026-2"
    )
    request = WorkflowSubmitRequest(
        competitor="Cursor", correlation_id="legacy-seed"
    ).to_analysis_request()
    with database.session_factory.begin() as session:
        session.add(
            WorkflowRecord(
                workflow_id=request.stable_workflow_id,
                request_fingerprint=request.fingerprint,
                competitor=request.competitor,
                status="success",
                partial_failure=False,
                payload={},
                request_payload=request.model_dump(mode="json"),
                progress=100,
                cancel_requested=False,
                attempt_count=1,
                max_attempts=2,
            )
        )
        session.add(
            WorkflowRecord(
                workflow_id="workflow_" + "b" * 24,
                request_fingerprint="a" * 64,
                competitor="Cursor",
                status="success",
                partial_failure=False,
                payload={},
                request_payload=None,
                progress=100,
                cancel_requested=False,
                attempt_count=1,
                max_attempts=2,
                submitted_at=None,
            )
        )

    first_seed = seed_demo_users(database)
    second_seed = seed_demo_users(database)
    assert first_seed["legacy_workflows_assigned"] == 2
    assert second_seed["legacy_workflows_assigned"] == 0
    with database.session_factory() as session:
        assert session.get(
            WorkflowRecord, request.stable_workflow_id
        ).user_id == first.user_id
        assert session.get(
            WorkflowRecord, "workflow_" + "b" * 24
        ).user_id == first.user_id
    database.dispose()
