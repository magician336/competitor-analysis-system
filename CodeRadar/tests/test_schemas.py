from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from schemas.document import (
    DimensionTag,
    EventType,
    EvidenceLevel,
    IndexStatus,
    RawRecord,
    SourceType,
    StructuredDocument,
)


def test_raw_record_normalizes_aliases_hash_and_utc_time() -> None:
    record = RawRecord(
        crawl_run_id="run-001",
        competitor="Cursor",
        source_type="official",
        url="HTTPS://cursor.com/",
        crawl_time="2026-07-13T16:00:00+08:00",
        status_code=200,
        raw_path="data/raw/cursor/official/run-001/page.html",
        payload_hash="A" * 64,
    )

    assert record.source_type is SourceType.OFFICIAL_PAGE
    assert record.fetched_at == datetime(2026, 7, 13, 8, tzinfo=timezone.utc)
    assert record.http_status == 200
    assert record.payload_hash == f"sha256:{'a' * 64}"
    assert record.raw_record_id.startswith("raw_")
    assert record.final_url == record.canonical_url == record.requested_url


def test_raw_record_id_is_stable_for_same_source_response() -> None:
    fields = {
        "crawl_run_id": "run-001",
        "competitor": "Cursor",
        "source_type": "official_page",
        "requested_url": "https://cursor.com/",
        "fetched_at": "2026-07-13T08:00:00Z",
        "payload_hash": "1" * 64,
    }

    first = RawRecord(**fields)
    second = RawRecord(**fields)
    assert first.raw_record_id == second.raw_record_id


def test_structured_document_generates_traceable_stable_identity() -> None:
    fields = {
        "raw_record_id": "raw_123",
        "raw_path": "data/raw/cursor/changelog/run/page.html",
        "competitor": "Cursor",
        "title": "Agent 2.0",
        "content": "Agent mode now uses repository context.",
        "source_type": "changelog",
        "evidence_level": "A",
        "url": "https://cursor.com/changelog/agent-2",
        "publish_time": "2026-07-01T08:30:00Z",
        "crawl_time": "2026-07-13T16:00:00+08:00",
        "event_type": "E2",
        "dimension_tags": ["D2", "D3", "D2"],
        "label_confidence": 0.9,
        "label_reasons": ["agent", "IDE", "agent"],
    }

    document = StructuredDocument(**fields)
    same_document = StructuredDocument(**fields)

    assert document.source_type is SourceType.OFFICIAL_CHANGELOG
    assert document.evidence_level is EvidenceLevel.A
    assert document.event_type is EventType.PRODUCT_RELEASE
    assert document.event_code == "E2"
    assert document.dimension_tags == [
        DimensionTag.AGENT_CONTEXT,
        DimensionTag.IDE_ECOSYSTEM,
    ]
    assert document.dimension_codes == ["D2", "D3"]
    assert document.index_status is IndexStatus.PENDING
    assert document.content_hash.startswith("sha256:")
    assert document.document_id == same_document.document_id
    assert document.version_id == same_document.version_id
    assert document.valid_from == document.publish_time
    assert document.label_reasons == ["agent", "IDE"]


def test_document_version_changes_when_content_changes_but_document_id_does_not() -> None:
    base = {
        "raw_record_id": "raw_123",
        "raw_path": "data/raw/cursor/pricing/run/page.html",
        "competitor": "Cursor",
        "title": "Pricing",
        "source_type": "pricing",
        "evidence_level": "A",
        "url": "https://cursor.com/pricing",
        "crawl_time": "2026-07-13T08:00:00Z",
    }

    old = StructuredDocument(content="Pro costs $20 per month.", **base)
    new = StructuredDocument(content="Pro costs $25 per month.", **base)

    assert old.document_id == new.document_id
    assert old.content_hash != new.content_hash
    assert old.version_id != new.version_id


def test_document_identity_prefers_canonical_competitor_id_from_metadata() -> None:
    base = {
        "raw_record_id": "raw_123",
        "raw_path": "data/raw/github_copilot/official/run/page.html",
        "title": "GitHub Copilot",
        "content": "AI coding assistant",
        "source_type": "official",
        "evidence_level": "A",
        "url": "https://github.com/features/copilot",
        "crawl_time": "2026-07-13T08:00:00Z",
        "source_metadata": {"competitor_id": "github_copilot"},
    }

    display_name = StructuredDocument(competitor="GitHub Copilot", **base)
    canonical_name = StructuredDocument(competitor="github_copilot", **base)

    assert display_name.document_id == canonical_name.document_id


def test_structured_document_rejects_invalid_validity_interval() -> None:
    with pytest.raises(ValidationError, match="valid_to"):
        StructuredDocument(
            raw_record_id="raw_123",
            raw_path="data/raw/cursor/official/run/page.html",
            competitor="Cursor",
            title="Cursor",
            content="AI coding agent",
            source_type="official",
            evidence_level="A",
            url="https://cursor.com/",
            crawl_time="2026-07-13T08:00:00Z",
            valid_from="2026-07-13T08:00:00Z",
            valid_to="2026-07-12T08:00:00Z",
        )


def test_structured_document_forbids_unknown_fields_and_invalid_confidence() -> None:
    base = {
        "raw_record_id": "raw_123",
        "raw_path": "data/raw/cursor/official/run/page.html",
        "competitor": "Cursor",
        "title": "Cursor",
        "content": "AI coding agent",
        "source_type": "official",
        "evidence_level": "A",
        "url": "https://cursor.com/",
        "crawl_time": "2026-07-13T08:00:00Z",
    }

    with pytest.raises(ValidationError):
        StructuredDocument(**base, unknown_field="unexpected")
    with pytest.raises(ValidationError):
        StructuredDocument(**base, label_confidence=1.1)
