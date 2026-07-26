from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import stat

from fastapi.testclient import TestClient
import pytest

from backend.main import create_app
from backend.services.admin_documents import MAX_UPLOAD_BYTES
from backend.services.rag_service import get_rag_service
from mini_rag.config import MiniRAGSettings
from schemas.document import StructuredDocument


def _document(
    *,
    content: str = "Initial product documentation.",
    url: str = "https://example.test/docs",
    publish_time: str = "2026-07-01T00:00:00Z",
    needs_review: bool = False,
    title: str = "Product documentation",
    competitor: str = "Cursor",
    source_type: str = "product_docs",
    dimension_tags: list[str] | None = None,
    is_current: bool = True,
    crawl_time: str = "2026-07-01T01:00:00Z",
) -> StructuredDocument:
    return StructuredDocument(
        raw_record_id="raw_test",
        raw_path="data/raw/test.txt",
        competitor=competitor,
        title=title,
        content=content,
        source_type=source_type,
        evidence_level="A",
        url=url,
        publish_time=publish_time,
        crawl_time=crawl_time,
        dimension_tags=dimension_tags or ["D1"],
        is_current=is_current,
        needs_review=needs_review,
    )


def _write_documents(path: Path, documents: list[StructuredDocument]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(document.model_dump_json() for document in documents) + "\n",
        encoding="utf-8",
    )


class FakeAdminService:
    def __init__(self, project_root: Path, *, index_error: Exception | None = None) -> None:
        self.settings = MiniRAGSettings(
            project_root=project_root,
            data={"documents_path": "data/cleaned/documents.jsonl"},
        )
        self.index_error = index_error
        self.index_calls = 0

    def build_index(self, documents_path, *, rebuild, delete_missing):
        self.index_calls += 1
        if self.index_error:
            raise self.index_error
        documents = [
            StructuredDocument.model_validate_json(line)
            for line in Path(documents_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return {
            "documents": len(documents),
            "chunks": len(documents),
            "report": {
                "indexed_count": 1,
                "skipped_count": max(0, len(documents) - 1),
                "failed_count": 0,
                "errors": [],
            },
        }

    def health(self):
        return {
            "status": "ready",
            "index": "test_chunks_current",
            "physical_index": "test_chunks_v1",
            "indexed_chunks": 17,
            "embedding_model": "hash-test",
            "embedding_dimension": 32,
            "index_embedding_model": "hash-test",
            "index_embedding_dimension": 32,
            "embedding_compatible": True,
            "backend": {"status": "green"},
        }


@pytest.fixture
def admin_client(tmp_path: Path):
    documents_path = tmp_path / "data" / "cleaned" / "documents.jsonl"
    _write_documents(documents_path, [_document()])
    service = FakeAdminService(tmp_path)
    app = create_app()
    app.dependency_overrides[get_rag_service] = lambda: service
    with TestClient(app) as client:
        yield client, service, documents_path


def _text_form(
    content: bytes = b"New agent documentation.",
    *,
    filename: str = "agent.md",
) -> tuple[dict[str, tuple[str, bytes, str]], dict[str, str]]:
    return (
        {"file": (filename, content, "text/markdown")},
        {
            "competitor": "Cursor",
            "title": "Agent guide",
            "source_url": "https://example.test/agent",
            "publish_time": "2026-07-20",
            "dimension_tags": '["D2", "ide_ecosystem"]',
        },
    )


def test_admin_overview_uses_isolated_document_dataset(admin_client) -> None:
    client, _service, _path = admin_client
    response = client.get("/api/admin/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["stats"] == {
        "documents_total": 1,
        "current_versions": 1,
        "historical_versions": 0,
        "pending_review": 0,
    }
    assert body["distributions"]["competitors"] == {"Cursor": 1}
    assert body["recent_documents"][0]["title"] == "Product documentation"
    assert body["dataset_updated_at"].endswith("Z")


def _document_listing_dataset() -> list[StructuredDocument]:
    return [
        _document(
            content="Current Cursor agent handbook.",
            url="https://example.test/cursor/agent",
            publish_time="2026-07-25T00:00:00Z",
            title="Agent Handbook",
            dimension_tags=["D2"],
        ),
        _document(
            content="Historical Cursor agent handbook.",
            url="https://example.test/cursor/agent",
            publish_time="2026-06-10T00:00:00Z",
            title="Agent Handbook Legacy",
            dimension_tags=["D2", "D3"],
            is_current=False,
        ),
        _document(
            content="Copilot release notes.",
            url="https://example.test/copilot/releases",
            publish_time="2026-07-24T00:00:00Z",
            title="Copilot Release Notes",
            competitor="GitHub Copilot",
            source_type="official_changelog",
            dimension_tags=["D1"],
            needs_review=True,
        ),
        _document(
            content="Cursor pricing details.",
            url="https://example.test/cursor/pricing",
            publish_time="2026-07-22T00:00:00Z",
            title="Cursor Pricing",
            source_type="pricing",
            dimension_tags=["D5"],
            needs_review=True,
        ),
        _document(
            content="Windsurf security details.",
            url="https://example.test/windsurf/security",
            publish_time="2026-07-20T00:00:00Z",
            title="Windsurf Security",
            competitor="Windsurf",
            source_type="security_privacy",
            dimension_tags=["D6"],
        ),
    ]


def test_admin_documents_returns_stable_newest_first_pages(admin_client) -> None:
    client, _service, documents_path = admin_client
    _write_documents(documents_path, _document_listing_dataset())

    first = client.get("/api/admin/documents", params={"page_size": 2})
    second = client.get("/api/admin/documents", params={"page": 2, "page_size": 2})

    assert first.status_code == 200
    assert first.json() == {
        "items": [
            {
                "document_id": _document_listing_dataset()[0].document_id,
                "version_id": _document_listing_dataset()[0].version_id,
                "title": "Agent Handbook",
                "competitor": "Cursor",
                "source_type": "product_docs",
                "publish_time": "2026-07-25T00:00:00Z",
                "dimension_tags": ["agent_context"],
                "is_current": True,
                "needs_review": False,
            },
            {
                "document_id": _document_listing_dataset()[2].document_id,
                "version_id": _document_listing_dataset()[2].version_id,
                "title": "Copilot Release Notes",
                "competitor": "GitHub Copilot",
                "source_type": "official_changelog",
                "publish_time": "2026-07-24T00:00:00Z",
                "dimension_tags": ["code_intelligence"],
                "is_current": True,
                "needs_review": True,
            },
        ],
        "page": 1,
        "page_size": 2,
        "total": 5,
        "total_pages": 3,
    }
    assert [item["title"] for item in second.json()["items"]] == [
        "Cursor Pricing",
        "Windsurf Security",
    ]


@pytest.mark.parametrize(
    ("status_filter", "expected_titles"),
    [
        (
            "current",
            [
                "Agent Handbook",
                "Copilot Release Notes",
                "Cursor Pricing",
                "Windsurf Security",
            ],
        ),
        ("historical", ["Agent Handbook Legacy"]),
        ("review", ["Copilot Release Notes", "Cursor Pricing"]),
    ],
)
def test_admin_documents_status_filters(
    admin_client,
    status_filter: str,
    expected_titles: list[str],
) -> None:
    client, _service, documents_path = admin_client
    _write_documents(documents_path, _document_listing_dataset())

    response = client.get("/api/admin/documents", params={"status": status_filter})

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == expected_titles
    assert response.json()["total"] == len(expected_titles)


def test_admin_documents_search_and_combined_filters_are_case_insensitive(
    admin_client,
) -> None:
    client, _service, documents_path = admin_client
    _write_documents(documents_path, _document_listing_dataset())

    response = client.get(
        "/api/admin/documents",
        params={
            "q": "AGENT",
            "status": "historical",
            "competitor": "cursor",
            "source_type": "PRODUCT_DOCS",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Agent Handbook Legacy"
    assert body["items"][0]["is_current"] is False


def test_admin_documents_empty_result_and_out_of_range_page(admin_client) -> None:
    client, _service, documents_path = admin_client
    _write_documents(documents_path, _document_listing_dataset())

    empty_filter = client.get("/api/admin/documents", params={"q": "not present"})
    empty_page = client.get(
        "/api/admin/documents",
        params={"page": 9, "page_size": 2},
    )

    assert empty_filter.status_code == 200
    assert empty_filter.json()["items"] == []
    assert empty_filter.json()["total"] == 0
    assert empty_filter.json()["total_pages"] == 0
    assert empty_page.status_code == 200
    assert empty_page.json()["items"] == []
    assert empty_page.json()["total"] == 5
    assert empty_page.json()["total_pages"] == 3


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"status": "archived"},
        {"source_type": "unknown_source"},
        {"q": "x" * 201},
        {"competitor": "x" * 201},
    ],
)
def test_admin_documents_rejects_invalid_query_parameters(
    admin_client,
    params: dict[str, str | int],
) -> None:
    client, _service, _documents_path = admin_client

    response = client.get("/api/admin/documents", params=params)

    assert response.status_code == 422


@pytest.mark.parametrize("filename", ["guide.md", "guide.txt"])
def test_markdown_and_text_import_persist_archive_backup_and_index(
    admin_client,
    filename: str,
) -> None:
    client, service, documents_path = admin_client
    files, data = _text_form(filename=filename)
    response = client.post("/api/admin/documents/import", files=files, data=data)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["persisted"] is True
    assert body["documents_imported"] == 1
    assert body["chunks_indexed"] == 1
    assert body["indexed_chunks_total"] == 17
    assert service.index_calls == 1

    rows = [
        StructuredDocument.model_validate_json(line)
        for line in documents_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
    imported = next(item for item in rows if item.title == "Agent guide")
    assert imported.evidence_level.value == "C"
    assert imported.dimension_codes == ["D2", "D3"]
    assert imported.needs_review is True
    assert (service.settings.project_root / imported.raw_path).is_file()
    assert list((documents_path.parent / "backups").glob("*.jsonl"))


def test_jsonl_import_uses_structured_document_fields(admin_client) -> None:
    client, _service, documents_path = admin_client
    incoming = _document(
        content="JSONL release notes.",
        url="https://example.test/release",
        publish_time="2026-07-23T00:00:00Z",
    )
    response = client.post(
        "/api/admin/documents/import",
        files={
            "file": (
                "records.jsonl",
                incoming.model_dump_json().encode("utf-8"),
                "application/x-ndjson",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["documents_imported"] == 1
    contents = documents_path.read_text(encoding="utf-8")
    assert "JSONL release notes." in contents


def test_import_atomically_replaces_read_only_dataset_and_restores_mode(
    admin_client,
) -> None:
    client, _service, documents_path = admin_client
    original_mode = stat.S_IMODE(documents_path.stat().st_mode)
    read_only_mode = original_mode & ~stat.S_IWRITE
    os.chmod(documents_path, read_only_mode)
    files, data = _text_form()

    response = client.post("/api/admin/documents/import", files=files, data=data)

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "New agent documentation." in documents_path.read_text(encoding="utf-8")
    assert not bool(stat.S_IMODE(documents_path.stat().st_mode) & stat.S_IWRITE)


def test_duplicate_upload_is_skipped_but_index_sync_runs(admin_client) -> None:
    client, service, _documents_path = admin_client
    incoming = _document()
    response = client.post(
        "/api/admin/documents/import",
        files={"file": ("records.jsonl", incoming.model_dump_json(), "application/x-ndjson")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["persisted"] is False
    assert body["documents_imported"] == 0
    assert body["documents_skipped"] == 1
    assert "index synchronization still ran" in body["warnings"][0]
    assert service.index_calls == 1


def test_new_version_marks_old_version_historical(admin_client) -> None:
    client, _service, documents_path = admin_client
    newer = _document(
        content="Updated product documentation.",
        publish_time="2026-07-25T00:00:00Z",
    )
    response = client.post(
        "/api/admin/documents/import",
        files={"file": ("records.jsonl", newer.model_dump_json(), "application/x-ndjson")},
    )

    assert response.status_code == 200
    rows = [
        StructuredDocument.model_validate_json(line)
        for line in documents_path.read_text(encoding="utf-8").splitlines()
    ]
    old = next(item for item in rows if item.content.startswith("Initial"))
    latest = next(item for item in rows if item.content.startswith("Updated"))
    assert old.document_id == latest.document_id
    assert old.is_current is False
    assert old.valid_to == datetime(2026, 7, 25, tzinfo=timezone.utc)
    assert old.index_status.value == "stale"
    assert latest.is_current is True


def test_optional_source_url_uses_stable_manual_identity_for_retry_and_new_version(
    admin_client,
) -> None:
    client, service, documents_path = admin_client
    files, data = _text_form()
    data.pop("source_url")
    first = client.post("/api/admin/documents/import", files=files, data=data)
    assert first.status_code == 200
    rows = [
        StructuredDocument.model_validate_json(line)
        for line in documents_path.read_text(encoding="utf-8").splitlines()
    ]
    first_import = next(item for item in rows if item.title == "Agent guide")
    assert first_import.url == "manual-upload://cursor/agent.md"

    files, data = _text_form()
    data.pop("source_url")
    duplicate = client.post("/api/admin/documents/import", files=files, data=data)
    assert duplicate.json()["documents_skipped"] == 1
    assert duplicate.json()["persisted"] is False

    files, data = _text_form(b"Changed agent documentation.")
    data.pop("source_url")
    updated = client.post("/api/admin/documents/import", files=files, data=data)
    assert updated.json()["documents_imported"] == 1
    rows = [
        StructuredDocument.model_validate_json(line)
        for line in documents_path.read_text(encoding="utf-8").splitlines()
    ]
    versions = [item for item in rows if item.document_id == first_import.document_id]
    assert len(versions) == 2
    assert sum(item.is_current for item in versions) == 1
    assert service.index_calls == 3


@pytest.mark.parametrize(
    ("filename", "content", "expected_detail"),
    [
        ("guide.pdf", b"content", "Only .md, .txt, and .jsonl"),
        ("guide.md", b"\xff\xfe\x00", "UTF-8"),
        ("records.jsonl", b'{"title": "missing required fields"}', "Invalid StructuredDocument"),
    ],
)
def test_invalid_uploads_do_not_modify_dataset(
    admin_client,
    filename: str,
    content: bytes,
    expected_detail: str,
) -> None:
    client, service, documents_path = admin_client
    before = documents_path.read_bytes()
    files, data = _text_form(content, filename=filename)
    response = client.post("/api/admin/documents/import", files=files, data=data)

    assert response.status_code == 422
    assert expected_detail in response.json()["detail"]
    assert documents_path.read_bytes() == before
    assert service.index_calls == 0


def test_oversize_upload_is_rejected_before_persistence(admin_client) -> None:
    client, service, documents_path = admin_client
    before = documents_path.read_bytes()
    files, data = _text_form(b"x" * (MAX_UPLOAD_BYTES + 1))
    response = client.post("/api/admin/documents/import", files=files, data=data)

    assert response.status_code == 413
    assert "5 MB" in response.json()["detail"]
    assert documents_path.read_bytes() == before
    assert service.index_calls == 0


def test_index_failure_returns_recoverable_partial_failure(tmp_path: Path) -> None:
    documents_path = tmp_path / "data" / "cleaned" / "documents.jsonl"
    _write_documents(documents_path, [_document()])
    service = FakeAdminService(tmp_path, index_error=RuntimeError("test index offline"))
    app = create_app()
    app.dependency_overrides[get_rag_service] = lambda: service
    files, data = _text_form()

    with TestClient(app) as client:
        response = client.post("/api/admin/documents/import", files=files, data=data)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial_failure"
    assert body["persisted"] is True
    assert body["documents_imported"] == 1
    assert body["indexed_chunks_total"] == 17
    assert "test index offline" in body["errors"][0]
    assert len(documents_path.read_text(encoding="utf-8").splitlines()) == 2


def test_persistence_failure_is_distinct_from_index_failure(
    admin_client,
    monkeypatch,
) -> None:
    client, service, _documents_path = admin_client
    files, data = _text_form()

    def fail_persistence(*_args, **_kwargs):
        raise OSError("test disk full")

    monkeypatch.setattr("backend.routers.admin.persist_documents", fail_persistence)
    response = client.post("/api/admin/documents/import", files=files, data=data)

    assert response.status_code == 500
    body = response.json()
    assert body["status"] == "save_failed"
    assert body["persisted"] is False
    assert "test disk full" in body["errors"][0]
    assert service.index_calls == 0
