from __future__ import annotations

import json
from datetime import datetime, timezone

import requests

from crawler.models import CollectorResult, CrawlSummary, SourceType
from crawler.storage import RawWriter


def test_raw_writer_persists_payload_and_traceable_sidecar(tmp_path) -> None:
    writer = RawWriter(tmp_path / "raw", "run:001")
    fetched_at = datetime(2026, 7, 13, 8, tzinfo=timezone.utc)

    record = writer.write_bytes(
        competitor="Cursor AI",
        source_type="official",
        requested_url="https://cursor.com/",
        canonical_url="https://cursor.com/",
        final_url="https://cursor.com/",
        payload=b"<html><main>Cursor Agent</main></html>",
        http_status=200,
        content_type="text/html; charset=utf-8",
        fetched_at=fetched_at,
        source_metadata={"evidence_level": "A"},
    )

    payload_path = writer.raw_root / record.payload_path
    metadata_path = payload_path.with_name(f"{record.raw_record_id}.meta.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert payload_path.read_bytes() == b"<html><main>Cursor Agent</main></html>"
    assert payload_path.parts[-4:-1] == ("Cursor_AI", "official", "run_001")
    assert metadata["raw_record_id"] == record.raw_record_id
    assert metadata["payload_path"] == record.payload_path
    assert metadata["payload_hash"] == record.payload_hash
    assert metadata["fetched_at"] == "2026-07-13T08:00:00Z"
    assert metadata["source_metadata"] == {"evidence_level": "A"}


def test_raw_writer_uses_stable_identity_for_same_payload_in_same_run(tmp_path) -> None:
    writer = RawWriter(tmp_path / "raw", "run-001")
    arguments = {
        "competitor": "Cursor",
        "source_type": "pricing",
        "requested_url": "https://cursor.com/pricing",
        "canonical_url": "https://cursor.com/pricing",
        "payload": b"Pro costs $20 per month.",
        "content_type": "text/plain",
    }

    first = writer.write_bytes(**arguments)
    second = writer.write_bytes(**arguments)

    assert first.raw_record_id == second.raw_record_id
    assert first.payload_path == second.payload_path


def test_write_response_for_304_creates_metadata_without_payload(tmp_path) -> None:
    writer = RawWriter(tmp_path / "raw", "run-001")
    response = requests.Response()
    response.status_code = 304
    response.url = "https://cursor.com/changelog"
    response.headers["ETag"] = '"v1"'

    record = writer.write_response(
        competitor="Cursor",
        source_type="changelog",
        requested_url=response.url,
        response=response,
    )

    assert record.http_status == 304
    assert record.payload_path is None
    metadata = next((tmp_path / "raw").rglob(f"{record.raw_record_id}.meta.json"))
    assert json.loads(metadata.read_text(encoding="utf-8"))["http_status"] == 304


def test_write_error_keeps_failure_auditable_without_payload(tmp_path) -> None:
    writer = RawWriter(tmp_path / "raw", "run-001")

    record = writer.write_error(
        competitor="Cursor",
        source_type="official",
        requested_url="https://cursor.com/",
        error="request timed out",
    )

    assert record.error == "request timed out"
    assert record.payload_path is None
    assert next((tmp_path / "raw").rglob(f"{record.raw_record_id}.meta.json"))


def test_crawl_summary_exit_codes_distinguish_success_partial_and_fatal(tmp_path) -> None:
    started = datetime(2026, 7, 13, 8, tzinfo=timezone.utc)
    record = RawWriter(tmp_path / "raw", "run").write_bytes(
        competitor="Cursor",
        source_type="official",
        requested_url="https://cursor.com/",
        canonical_url="https://cursor.com/",
        payload=b"Cursor",
        content_type="text/plain",
    )
    success = CollectorResult("Cursor", SourceType.OFFICIAL, records=[record])
    empty_success = CollectorResult("Cursor", SourceType.CHANGELOG)
    failed = CollectorResult("Cursor", SourceType.PRICING, errors=["one source failed"])

    assert CrawlSummary("run", started, results=[success]).exit_code == 0
    assert CrawlSummary("run", started, results=[success, failed]).exit_code == 2
    assert CrawlSummary("run", started, results=[empty_success, failed]).exit_code == 2
    assert CrawlSummary("run", started, results=[failed]).exit_code == 1
    assert CrawlSummary(
        "run",
        started,
        configuration_errors=["invalid configuration"],
    ).exit_code == 1
