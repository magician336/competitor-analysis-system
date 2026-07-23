from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crawler.models import SourceType as CrawlerSourceType
from crawler.storage import RawWriter
from processing.audit import REVIEW_AGENT, audit_corpus
from processing.labeling import RuleLabeler
from processing.normalizers import sha256_text
from schemas.document import EventType, StructuredDocument
from scripts.ai_precheck_documents import main as precheck_main


NOW = datetime(2026, 7, 17, 4, 0, tzinfo=timezone.utc)


def _write_document(
    project_root: Path,
    *,
    content: str,
    title: str = "Agent workspace capability",
    url: str = "https://example.com/product",
    source_type: str = "official_page",
    current: bool = True,
    valid_to: datetime | None = None,
    raw_suffix: str = "one",
) -> StructuredDocument:
    raw_root = project_root / "data" / "raw"
    writer = RawWriter(raw_root, f"run-{raw_suffix}")
    crawler_source = {
        "official_page": CrawlerSourceType.OFFICIAL,
        "official_changelog": CrawlerSourceType.CHANGELOG,
        "pricing": CrawlerSourceType.PRICING,
        "github_release": CrawlerSourceType.GITHUB_RELEASE,
        "github_issue": CrawlerSourceType.GITHUB_ISSUE,
    }[source_type]
    record = writer.write_bytes(
        competitor="cursor",
        source_type=crawler_source,
        requested_url=url,
        canonical_url=url,
        final_url=url,
        payload=content.encode("utf-8"),
        content_type="text/plain",
        source_metadata={"competitor_name": "Cursor", "evidence_level": "A"},
        fetched_at=NOW - timedelta(days=1),
    )
    labels = RuleLabeler().label(source_type, title, content)
    raw_path = (raw_root / str(record.payload_path)).relative_to(project_root).as_posix()
    return StructuredDocument(
        raw_record_id=record.raw_record_id,
        raw_path=raw_path,
        competitor="Cursor",
        title=title,
        content=content,
        source_type=source_type,
        evidence_level="A",
        url=url,
        crawl_time=record.fetched_at,
        event_type=labels.event_type,
        dimension_tags=labels.dimension_tags,
        content_hash=sha256_text(content),
        label_confidence=labels.label_confidence,
        label_reasons=labels.label_reasons,
        needs_review=labels.needs_review,
        source_metadata={
            "competitor_id": "cursor",
            "payload_hash": record.payload_hash and f"sha256:{record.payload_hash}",
            "needs_browser": False,
        },
        is_current=current,
        valid_to=valid_to,
    )


def _write_jsonl(path: Path, documents: list[StructuredDocument]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(document.model_dump_json() + "\n" for document in documents),
        encoding="utf-8",
    )


def test_audit_passes_a_traceable_well_formed_document(tmp_path: Path) -> None:
    content = (
        "Agent mode understands the workspace and repository context, then uses the terminal "
        "to complete a multi-file task. " * 3
    )
    document = _write_document(tmp_path, content=content)
    path = tmp_path / "data" / "cleaned" / "documents.jsonl"
    _write_jsonl(path, [document])

    result = audit_corpus(path, project_root=tmp_path, labeler=RuleLabeler(), now=NOW)

    assert result.valid_document_count == 1
    assert result.audits[0].overall_decision == "PASS", result.audits[0].reason_codes
    assert result.audits[0].review_agent == REVIEW_AGENT
    assert result.audits[0].dimension_evidence["agent_context"]
    assert not any(key.startswith("human_") for key in result.audits[0].to_dict())


def test_audit_detects_hash_url_event_and_raw_failures(tmp_path: Path) -> None:
    content = "Agent workspace and repository context are available. " * 6
    document = _write_document(
        tmp_path,
        content=content,
        url="https://EXAMPLE.com/product/?utm_source=test",
    )
    document.content_hash = "sha256:" + "0" * 64
    document.event_type = EventType.PRICING_CHANGE
    raw_path = tmp_path / document.raw_path
    raw_path.write_text("tampered payload", encoding="utf-8")
    path = tmp_path / "data" / "cleaned" / "documents.jsonl"
    _write_jsonl(path, [document])

    audit = audit_corpus(path, project_root=tmp_path, labeler=RuleLabeler(), now=NOW).audits[0]

    assert audit.overall_decision == "FAIL"
    assert {
        "CONTENT_HASH_MISMATCH",
        "URL_NOT_CANONICAL",
        "EVENT_TYPE_MISMATCH",
        "PAYLOAD_HASH_MISMATCH",
    }.issubset(audit.reason_codes)


def test_audit_queues_short_noisy_sensitive_content(tmp_path: Path) -> None:
    content = "Accept all cookies\ncontact person@example.com\nAgent"
    document = _write_document(tmp_path, content=content)
    path = tmp_path / "data" / "cleaned" / "documents.jsonl"
    _write_jsonl(path, [document])

    audit = audit_corpus(path, project_root=tmp_path, labeler=RuleLabeler(), now=NOW).audits[0]

    assert audit.overall_decision == "NEEDS_REVIEW"
    assert "CONTENT_TOO_SHORT" in audit.reason_codes
    assert "NOISE_COOKIE_BANNER" in audit.reason_codes
    assert "SENSITIVE_EMAIL" in audit.reason_codes
    assert audit.sensitive_data_flags == ["EMAIL"]


def test_phone_detection_ignores_dates_and_keeps_plausible_numbers(
    tmp_path: Path,
) -> None:
    date_only = _write_document(
        tmp_path,
        content="Released on 2026-05-03 and updated at 2025-08-24 094620. " * 4,
        raw_suffix="date",
    )
    phone = _write_document(
        tmp_path,
        content=("Contact +52 1 55 3217 2252 or 17636121051 for support. " * 4),
        raw_suffix="phone",
    )
    path = tmp_path / "data" / "cleaned" / "documents.jsonl"
    _write_jsonl(path, [date_only, phone])
    audits = audit_corpus(
        path,
        project_root=tmp_path,
        labeler=RuleLabeler(),
        now=NOW,
    ).audits
    date_audit, phone_audit = audits

    assert "PHONE" not in date_audit.sensitive_data_flags
    assert "PHONE" in phone_audit.sensitive_data_flags


def test_audit_reports_duplicate_versions_and_multiple_current_versions(tmp_path: Path) -> None:
    first = _write_document(tmp_path, content="Agent repository context. " * 12, raw_suffix="a")
    second = _write_document(tmp_path, content="Agent terminal workspace. " * 12, raw_suffix="b")
    second.document_id = first.document_id
    second.version_id = "ver_second"
    path = tmp_path / "data" / "cleaned" / "documents.jsonl"
    _write_jsonl(path, [first, first, second])

    result = audit_corpus(path, project_root=tmp_path, labeler=RuleLabeler(), now=NOW)
    by_version = {item.version_id: item for item in result.audits}

    assert len(result.audits) == 2
    assert "DUPLICATE_VERSION_ID" in by_version[first.version_id].reason_codes
    assert "MULTIPLE_CURRENT_VERSIONS" in by_version[first.version_id].reason_codes
    assert "MULTIPLE_CURRENT_VERSIONS" in by_version[second.version_id].reason_codes


def test_cli_writes_four_auditable_outputs_without_human_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    content = "Agent workspace repository context and terminal support. " * 6
    document = _write_document(tmp_path, content=content)
    documents = tmp_path / "data" / "cleaned" / "documents.jsonl"
    _write_jsonl(documents, [document])
    output_root = tmp_path / "runtime"

    monkeypatch.setattr("scripts.ai_precheck_documents.PROJECT_ROOT", tmp_path)
    exit_code = precheck_main(
        [
            "--documents",
            str(documents),
            "--output-root",
            str(output_root),
            "--audit-run-id",
            "unit-test",
        ]
    )

    output = output_root / "unit-test"
    assert exit_code == 0
    assert {item.name for item in output.iterdir()} == {
        "manifest.json",
        "documents.audit.jsonl",
        "review-queue.csv",
        "summary.json",
    }
    row = json.loads((output / "documents.audit.jsonl").read_text(encoding="utf-8"))
    assert row["overall_decision"] == "PASS"
    assert row["review_agent"] == REVIEW_AGENT
    assert not any(key.startswith("human_") for key in row)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert not any(key.startswith("human_") for key in manifest)
    assert "independent human acceptance" in manifest["acceptance_boundary"]
    assert manifest["valid_document_count"] == 1


def test_schema_invalid_row_is_preserved_as_failed_evidence(tmp_path: Path) -> None:
    path = tmp_path / "data" / "cleaned" / "documents.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"version_id":"ver_bad","content":""}\n', encoding="utf-8")

    result = audit_corpus(path, project_root=tmp_path, labeler=RuleLabeler(), now=NOW)

    assert result.valid_document_count == 0
    assert result.audits[0].version_id == "ver_bad"
    assert result.audits[0].overall_decision == "FAIL"
    assert result.audits[0].reason_codes == ["SCHEMA_INVALID"]
