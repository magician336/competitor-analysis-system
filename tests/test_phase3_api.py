from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.api_repository import FormalApiRepository, get_formal_api_repository
from backend.artifact_import import initialize_database
from backend.codemate_seed import seed_codemate_snapshot
from backend.database import Database
from backend.main import create_app
from backend.models import ApiAuditEventRecord, Base, CapabilitySnapshotRecord
from backend.repositories import AnalysisRepository
from backend.services.rag_service import get_rag_service
from tests.test_rag_api import FakeService


@pytest.fixture
def phase3_repository(tmp_path: Path) -> FormalApiRepository:
    database = Database(f"sqlite:///{(tmp_path / 'phase3.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    analysis = AnalysisRepository(database.session_factory)
    initialize_database(
        analysis,
        competitor_config=Path("config/competitors.yaml"),
        artifact_root=Path("artifacts/week3"),
    )
    seed_codemate_snapshot(analysis)
    repository = FormalApiRepository(database.session_factory)
    try:
        yield repository
    finally:
        database.dispose()


def _client(repository: FormalApiRepository) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_formal_api_repository] = lambda: repository
    app.dependency_overrides[get_rag_service] = lambda: FakeService()
    return TestClient(app)


def test_codemate_seed_and_formal_query_contracts(phase3_repository: FormalApiRepository) -> None:
    seed_codemate_snapshot(phase3_repository.analysis)
    seed_codemate_snapshot(phase3_repository.analysis)
    with phase3_repository.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(CapabilitySnapshotRecord).where(
                CapabilitySnapshotRecord.snapshot_id == "snap_5ac35eaa2c0c351115ec"
            )
        ) == 1
    client = _client(phase3_repository)
    snapshots = client.get("/api/snapshots", params={"competitor": "CodeMate Campus"})
    assert snapshots.status_code == 200
    assert snapshots.json()["total"] == 1
    item = snapshots.json()["items"][0]
    assert item["source_kind"] == "product_definition"
    detail = client.get(f"/api/snapshots/{item['snapshot_id']}").json()
    assert detail["snapshot"]["total_score"] == 41.04
    assert detail["snapshot"]["coverage_ratio"] == 0.56
    assert detail["snapshot"]["overall_confidence"] == 0.326
    assert detail["provenance"]["planning_only"] is True

    first = client.get(
        "/api/cards",
        params={"page": 1, "page_size": 3, "review_required": "false"},
    )
    second = client.get(
        "/api/cards",
        params={"page": 2, "page_size": 3, "review_required": "false"},
    )
    assert first.status_code == second.status_code == 200
    first_ids = {item["card_id"] for item in first.json()["items"]}
    second_ids = {item["card_id"] for item in second.json()["items"]}
    assert first_ids.isdisjoint(second_ids)
    card_id = first.json()["items"][0]["card_id"]
    card = client.get(f"/api/cards/{card_id}").json()
    assert card["card"]["card_id"] == card_id
    summary = next(item for item in first.json()["items"] if item["card_id"] == card_id)
    publish_times = sorted(
        item["publish_time"]
        for item in card["evidence_links"]
        if item["publish_time"] is not None
    )
    assert summary["publish_time"] == (publish_times[-1] if publish_times else None)
    if card["evidence_links"]:
        chunk_id = card["evidence_links"][0]["chunk_id"]
        evidence = client.get(f"/api/evidence/{chunk_id}")
        assert evidence.status_code == 200
        assert card_id in evidence.json()["card_ids"]
        assert evidence.json()["evidence"]["document_id"]
        assert evidence.json()["evidence"]["version_id"]
        assert evidence.json()["evidence"]["url"].startswith("http")

    invalid = client.get("/api/cards", params={"page_size": 101})
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")


def test_comparison_is_idempotent_and_planning_only(phase3_repository: FormalApiRepository) -> None:
    client = _client(phase3_repository)
    page = client.get(
        "/api/snapshots",
        params={"snapshot_from": "2026-07-20", "snapshot_to": "2026-07-20", "page_size": 100},
    ).json()
    snapshot_ids = [item["snapshot_id"] for item in page["items"]]
    assert len(snapshot_ids) == 6
    payload = {"snapshot_ids": snapshot_ids, "baseline_product": "CodeMate Campus"}
    first = client.post("/api/comparisons", json=payload)
    second = client.post("/api/comparisons", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["comparison_id"] == second.json()["comparison_id"]
    assert first.json()["official_ranking_ready"] is False
    assert first.json()["provenance"]["baseline_source_kind"] == "product_definition"
    latest = client.get("/api/comparisons/latest")
    assert latest.status_code == 200
    assert latest.json()["comparison_id"] == first.json()["comparison_id"]
    listing = client.get("/api/comparisons").json()
    assert listing["total"] == 1

    codemate = phase3_repository.snapshot_detail("snap_5ac35eaa2c0c351115ec").snapshot
    incompatible = codemate.model_copy(
        update={
            "snapshot_id": "snap_phase3_incompatible",
            "competitor": "Incompatible Product",
            "window_end": datetime(2026, 7, 21, tzinfo=timezone.utc),
        }
    )
    phase3_repository.analysis.save_snapshot(incompatible)
    conflict = client.post(
        "/api/comparisons",
        json={
            "snapshot_ids": [codemate.snapshot_id, incompatible.snapshot_id],
            "baseline_product": "CodeMate Campus",
        },
    )
    assert conflict.status_code == 409


def test_briefing_workflow_competitor_and_dimension_api(phase3_repository: FormalApiRepository) -> None:
    client = _client(phase3_repository)
    briefings = client.get("/api/briefings", params={"page_size": 2}).json()
    assert briefings["total"] == 5
    briefing_id = briefings["items"][0]["briefing_id"]
    download = client.get(f"/api/briefings/{briefing_id}/content")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/markdown")
    assert download.headers["content-disposition"].endswith('.md"')

    workflows = client.get("/api/workflows", params={"status": "success"}).json()
    assert workflows["total"] == 5
    assert all(item["status"] == "success" for item in workflows["items"])
    assert len(client.get("/api/dimensions").json()) == 7

    created = client.post(
        "/api/competitors",
        json={"id": "demo_ai", "name": "Demo AI", "aliases": [], "sources": {}},
    )
    assert created.status_code == 201
    duplicate = client.post(
        "/api/competitors",
        json={"id": "demo_ai", "name": "Demo AI 2", "aliases": [], "sources": {}},
    )
    assert duplicate.status_code == 409
    disabled = client.delete("/api/competitors/demo_ai")
    assert disabled.status_code == 204
    all_competitors = client.get("/api/competitors").json()
    assert next(item for item in all_competitors if item["id"] == "demo_ai")["enabled"] is False
    enabled = client.put(
        "/api/competitors/demo_ai",
        json={"name": "Demo AI", "aliases": ["Demo"], "sources": {}, "enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True


def test_api_key_rate_limit_problem_details_and_safe_audit(
    phase3_repository: FormalApiRepository,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODERADAR_AUTH_ENABLED", "true")
    monkeypatch.setenv("CODERADAR_API_KEY", "phase3-secret-key")
    monkeypatch.setenv("CODERADAR_READ_RATE_PER_MINUTE", "2")
    client = _client(phase3_repository)

    missing = client.get("/api/cards")
    wrong = client.get("/api/cards", headers={"X-API-Key": "wrong"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["code"] == "invalid_api_key"
    headers = {"X-API-Key": "phase3-secret-key", "X-Request-ID": "phase3-test"}
    assert client.get("/api/cards", headers=headers).status_code == 200
    not_found = client.get("/api/evidence/chunk_missing", headers=headers)
    assert not_found.status_code == 404
    assert not_found.json()["request_id"] == "phase3-test"
    limited = client.get("/api/cards", headers=headers)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1

    with phase3_repository.session_factory() as session:
        audits = list(session.scalars(select(ApiAuditEventRecord)))
        assert audits
        serialized = json.dumps(
            [
                {
                    "action": item.action,
                    "path": item.path,
                    "resource_id": item.resource_id,
                }
                for item in audits
            ],
            ensure_ascii=False,
        )
    assert "phase3-secret-key" not in serialized
    assert "markdown" not in serialized.casefold()


def test_openapi_has_security_and_no_benchmark_fields(phase3_repository: FormalApiRepository) -> None:
    client = _client(phase3_repository)
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["ApiKeyAuth"]["name"] == "X-API-Key"
    assert schema["paths"]["/api/cards"]["get"]["security"] == [{"ApiKeyAuth": []}]
    request_fields = schema["components"]["schemas"]["ComparisonCreateRequest"]["properties"]
    assert not any("benchmark" in name.casefold() for name in request_fields)
    table_names = set(Base.metadata.tables)
    assert not any("benchmark" in name.casefold() for name in table_names)
