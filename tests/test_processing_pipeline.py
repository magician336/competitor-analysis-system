from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from crawler.storage import RawWriter
from processing.pipeline import ProcessingPipeline, process_raw_records
from schemas.document import (
    DimensionTag,
    EventType,
    EvidenceLevel,
    IndexStatus,
    SourceType,
    StructuredDocument,
)


def _meta_path(raw_root, raw_record_id):
    return next(raw_root.rglob(f"{raw_record_id}.meta.json"))


def test_raw_html_to_documents_jsonl_end_to_end(tmp_path, fixture_text) -> None:
    raw_root = tmp_path / "data" / "raw"
    writer = RawWriter(raw_root, "run-001")
    record = writer.write_bytes(
        competitor="Cursor",
        source_type="changelog",
        requested_url="https://cursor.com/changelog/agent-2",
        canonical_url="https://cursor.com/changelog/agent-2",
        final_url="https://cursor.com/changelog/agent-2",
        payload=fixture_text("pages/changelog.html").encode(),
        http_status=200,
        content_type="text/html; charset=utf-8",
        source_metadata={"evidence_level": "A"},
    )
    pipeline = ProcessingPipeline(project_root=tmp_path)

    result = pipeline.process()

    assert result.succeeded is True
    assert result.scanned == result.processed_records == result.generated_documents == 1
    assert result.added == 1
    rows = result.output_path.read_text(encoding="utf-8").splitlines()
    [document] = [StructuredDocument.model_validate_json(row) for row in rows]
    assert document.raw_record_id == record.raw_record_id
    assert (tmp_path / document.raw_path).read_text(encoding="utf-8").startswith(
        "<!doctype html>"
    )
    assert document.source_type is SourceType.OFFICIAL_CHANGELOG
    assert document.event_type is EventType.PRODUCT_RELEASE
    assert DimensionTag.AGENT_CONTEXT in document.dimension_tags
    assert DimensionTag.PERFORMANCE_COST in document.dimension_tags
    assert document.language == "en"
    assert document.is_current is True
    assert document.index_status is IndexStatus.PENDING
    assert document.source_metadata["crawl_run_id"] == "run-001"
    assert document.source_metadata["payload_hash"].startswith("sha256:")


@pytest.mark.parametrize(
    ("raw_source", "document_source", "evidence", "event"),
    [
        ("product_docs", SourceType.PRODUCT_DOCS, EvidenceLevel.A, None),
        ("status_page", SourceType.STATUS_PAGE, EvidenceLevel.A, EventType.RISK_EXPERIENCE),
        ("plugin_marketplace", SourceType.PLUGIN_MARKETPLACE, EvidenceLevel.B, EventType.RISK_EXPERIENCE),
        ("community", SourceType.COMMUNITY, EvidenceLevel.C, EventType.RISK_EXPERIENCE),
        ("review", SourceType.REVIEW, EvidenceLevel.C, EventType.RISK_EXPERIENCE),
        ("security_privacy", SourceType.SECURITY_PRIVACY, EvidenceLevel.A, None),
        ("benchmark", SourceType.BENCHMARK, EvidenceLevel.B, None),
    ],
)
def test_extended_page_sources_survive_processing_with_source_policy(
    tmp_path,
    raw_source,
    document_source,
    evidence,
    event,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-sources").write_bytes(
        competitor="cursor",
        source_type=raw_source,
        requested_url=f"https://example.test/{raw_source}",
        canonical_url=f"https://example.test/{raw_source}",
        payload=(
            "<html><head><title>Source evidence</title></head><body><main>"
            "<h1>Source evidence</h1><p>The coding agent supports repository context "
            "and IDE integration for software teams.</p></main></body></html>"
        ).encode(),
        content_type="text/html",
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    [document] = _version_rows(result.output_path)
    assert document.source_type is document_source
    assert document.evidence_level is evidence
    assert document.event_type is event


def test_rss_source_survives_processing_as_product_release(tmp_path) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-rss").write_json(
        competitor="trae",
        source_type="rss",
        requested_url="https://example.test/rss.xml",
        canonical_url="https://example.test/blog/work-update",
        payload={
            "kind": "rss_entry",
            "feed_url": "https://example.test/rss.xml",
            "entry": {
                "id": "trae-work-2026-07-01",
                "title": "TRAE Work update",
                "link": "https://example.test/blog/work-update",
                "published_at": "2026-07-01T16:00:00Z",
                "updated_at": None,
                "author": "TRAE Team",
                "summary": (
                    "The coding agent adds repository context and IDE support."
                ),
                "content": [],
                "tags": ["product"],
            },
        },
        source_metadata={"evidence_level": "B", "format": "feed_entry"},
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    [document] = _version_rows(result.output_path)
    assert document.source_type is SourceType.RSS
    assert document.evidence_level is EvidenceLevel.B
    assert document.event_type is EventType.PRODUCT_RELEASE
    assert document.publish_time == datetime(2026, 7, 1, 16, tzinfo=timezone.utc)
    assert (
        document.source_metadata["identity_key"]
        == "feed-entry:trae-work-2026-07-01"
    )


def test_community_feed_and_json_topic_share_one_document_identity(tmp_path) -> None:
    raw_root = tmp_path / "data" / "raw"
    writer = RawWriter(raw_root, "run-community")
    feed = writer.write_bytes(
        competitor="cursor",
        source_type="community",
        requested_url="https://forum.cursor.com/latest.rss",
        canonical_url="https://forum.cursor.com/latest.rss",
        payload=(
            "<rss><channel><item><guid>topic-42</guid>"
            "<title>Agent debugging</title>"
            "<link>https://forum.cursor.com/t/agent-debugging/42</link>"
            "<pubDate>Sun, 20 Jul 2026 08:30:00 GMT</pubDate>"
            "<description>Agent stops after a tool call.</description>"
            "</item></channel></rss>"
        ).encode(),
        content_type="application/rss+xml",
    )
    json_page = writer.write_json(
        competitor="cursor",
        source_type="community",
        requested_url="https://forum.cursor.com/latest.json?page=0",
        canonical_url="https://forum.cursor.com/latest.json?page=0",
        payload={
            "topic_list": {
                "topics": [
                    {
                        "id": 42,
                        "slug": "agent-debugging",
                        "fancy_title": "Agent debugging",
                        "excerpt": "Agent stops after a tool call.",
                        "created_at": "2026-07-20T08:30:00Z",
                    }
                ]
            }
        },
        source_metadata={
            "format": "json",
            "json_items": {
                "result_path": "topic_list.topics",
                "title_field": "fancy_title",
                "content_fields": ["excerpt"],
                "url_template": "/t/{slug}/{id}",
                "url_base": "https://forum.cursor.com",
                "publish_time_field": "created_at",
                "identity_template": "topic:{id}",
                "identity_mode": "configured",
            },
        },
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [
            _meta_path(raw_root, feed.raw_record_id),
            _meta_path(raw_root, json_page.raw_record_id),
        ]
    )

    [document] = _version_rows(result.output_path)
    assert result.generated_documents == 2
    assert document.url == "https://forum.cursor.com/t/agent-debugging/42"
    assert document.source_metadata["identity_key"] == (
        "discourse-topic:https://forum.cursor.com:42"
    )


def test_configured_event_effective_time_overrides_page_publish_metadata(
    tmp_path,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    effective_at = "2026-05-20T23:00:00+08:00"
    record = RawWriter(raw_root, "run-pricing").write_bytes(
        competitor="tongyi_lingma",
        source_type="pricing",
        requested_url="https://example.test/pricing",
        canonical_url="https://example.test/pricing",
        payload=(
            "<html><head><meta property='article:published_time' "
            "content='2024-06-07T01:50:00Z'><title>计费说明</title></head>"
            "<body><main><h1>计费说明</h1><p>新定价方案自 2026 年 5 月 20 日起生效，"
            "专业版按月订阅并使用 Credits 额度。</p></main></body></html>"
        ).encode(),
        content_type="text/html",
        source_metadata={
            "evidence_level": "A",
            "event_effective_at": effective_at,
            "event_effective_at_basis": "Published pricing-page statement.",
        },
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    [document] = _version_rows(result.output_path)
    expected = datetime(2026, 5, 20, 15, tzinfo=timezone.utc)
    assert document.event_type is EventType.PRICING_CHANGE
    assert document.publish_time == expected
    assert document.valid_from == expected
    assert (
        document.source_metadata["publish_time_basis"]
        == "configured_event_effective_at"
    )


def test_empty_feed_is_an_expected_processing_skip(tmp_path) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-empty-feed").write_bytes(
        competitor="trae",
        source_type="rss",
        requested_url="https://example.test/rss.xml",
        canonical_url="https://example.test/rss.xml",
        payload=b"<rss><channel></channel></rss>",
        content_type="application/rss+xml",
        source_metadata={
            "format": "feed",
            "record_role": "feed_empty",
            "entry_count": 0,
        },
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    assert result.succeeded is True
    assert result.skipped == 1
    assert result.generated_documents == 0


def test_configured_json_item_list_materializes_marketplace_releases(
    tmp_path,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-json-releases").write_json(
        competitor="codegeex",
        source_type="changelog",
        requested_url="https://plugins.jetbrains.com/api/plugins/20587/updates",
        canonical_url="https://plugins.jetbrains.com/api/plugins/20587/updates",
        payload=[
            {
                "id": 910732,
                "link": (
                    "/plugin/20587-codegeex-ai-coding-assistant/"
                    "versions/stable/910732"
                ),
                "version": "2.27.4-223",
                "cdate": "1765426803000",
                "notes": "<ul><li>Add agent.</li></ul>",
                "sinceUntil": "223.0 — 253.*",
                "author": {"name": "codegeex team"},
            }
        ],
        source_metadata={
            "evidence_level": "B",
            "format": "json",
            "json_items": {
                "title_field": "version",
                "content_fields": ["notes", "sinceUntil"],
                "url_field": "link",
                "url_base": "https://plugins.jetbrains.com",
                "publish_time_field": "cdate",
                "author_field": "author.name",
                "identity_field": "id",
            },
        },
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    [document] = _version_rows(result.output_path)
    assert document.source_type is SourceType.OFFICIAL_CHANGELOG
    assert document.event_type is EventType.PRODUCT_RELEASE
    assert document.product_version == "2.27.4-223"
    assert document.url.endswith("/versions/stable/910732")
    assert document.publish_time == datetime(
        2025,
        12,
        11,
        4,
        20,
        3,
        tzinfo=timezone.utc,
    )
    assert document.author == "codegeex team"
    assert document.source_metadata["identity_key"] == "configured-json:910732"
    assert document.source_metadata["configured_identity"] == "910732"


def test_pipeline_uses_configured_name_evidence_and_dimension_rules(
    tmp_path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "dimensions.yaml").write_text(
        """\
version: 1
review_threshold: 0.55
dimensions:
  education_fit:
    code: D7
    keywords: [cohortmarker]
""",
        encoding="utf-8",
    )
    html = """
    <html><head><title>Program</title></head><body><main>
    <h1>Program</h1><p>The cohortmarker program supports guided learning.</p>
    </main></body></html>
    """
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-001").write_bytes(
        competitor="cursor",
        source_type="official",
        requested_url="https://cursor.com/program",
        canonical_url="https://cursor.com/program",
        payload=html.encode(),
        content_type="text/html",
        source_metadata={
            "competitor_name": "Cursor",
            "evidence_level": "B",
        },
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    [document] = _version_rows(result.output_path)
    assert document.competitor == "Cursor"
    assert document.source_metadata["competitor_id"] == "cursor"
    assert document.evidence_level is EvidenceLevel.B
    assert document.dimension_tags == [DimensionTag.EDUCATION_FIT]
    assert document.needs_review is False


def test_materialized_rss_entry_roundtrips_with_entry_identity(tmp_path) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-001").write_json(
        competitor="GitHub Copilot",
        source_type="changelog",
        requested_url="https://example.test/changelog.xml",
        canonical_url="https://example.test/changelog/copilot-agent",
        final_url="https://example.test/changelog.xml",
        http_status=200,
        published_at=datetime(2026, 7, 1, 8, 30, tzinfo=timezone.utc),
        payload={
            "kind": "changelog_entry",
            "feed_url": "https://example.test/changelog.xml",
            "entry": {
                "id": "copilot-agent-2026-07-01",
                "title": "Copilot coding agent update",
                "link": "https://example.test/changelog/copilot-agent",
                "published_at": "2026-07-01T08:30:00Z",
                "updated_at": None,
                "author": "GitHub",
                "summary": "<p>The coding agent can now edit multiple files.</p>",
                "content": [],
                "tags": ["copilot"],
            },
        },
        source_metadata={"format": "feed_entry"},
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    assert result.succeeded is True
    [document] = _version_rows(result.output_path)
    assert document.title == "Copilot coding agent update"
    assert document.url == "https://example.test/changelog/copilot-agent"
    assert document.author == "GitHub"
    assert document.publish_time == datetime(2026, 7, 1, 8, 30, tzinfo=timezone.utc)
    assert document.content == "The coding agent can now edit multiple files."


def test_reprocessing_same_raw_record_does_not_duplicate_version(
    tmp_path,
    fixture_text,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-001").write_bytes(
        competitor="Cursor",
        source_type="official",
        requested_url="https://cursor.com/",
        canonical_url="https://cursor.com/",
        payload=fixture_text("pages/official.html").encode(),
        content_type="text/html",
    )
    meta_path = _meta_path(raw_root, record.raw_record_id)
    pipeline = ProcessingPipeline(project_root=tmp_path)

    first = pipeline.process([meta_path])
    second = pipeline.process([meta_path])

    assert first.added == 1
    assert second.added == 0
    assert second.unchanged == 1
    assert len(pipeline.version_store.load()) == 1


def test_changed_payload_creates_new_current_version(tmp_path, fixture_text) -> None:
    raw_root = tmp_path / "data" / "raw"
    first = RawWriter(raw_root, "run-001").write_bytes(
        competitor="Cursor",
        source_type="pricing",
        requested_url="https://cursor.com/pricing",
        canonical_url="https://cursor.com/pricing",
        payload=fixture_text("pages/pricing.html").encode(),
        content_type="text/html",
        fetched_at=datetime(2026, 7, 1, 8, tzinfo=timezone.utc),
    )
    changed_html = fixture_text("pages/pricing.html").replace("$20", "$25")
    second = RawWriter(raw_root, "run-002").write_bytes(
        competitor="Cursor",
        source_type="pricing",
        requested_url="https://cursor.com/pricing",
        canonical_url="https://cursor.com/pricing",
        payload=changed_html.encode(),
        content_type="text/html",
        fetched_at=datetime(2026, 7, 10, 8, tzinfo=timezone.utc),
    )
    pipeline = ProcessingPipeline(project_root=tmp_path)

    pipeline.process([_meta_path(raw_root, first.raw_record_id)])
    result = pipeline.process([_meta_path(raw_root, second.raw_record_id)])

    assert result.added == 1
    assert result.superseded == 1
    old, current = pipeline.version_store.load()
    assert old.document_id == current.document_id
    assert old.is_current is False
    assert old.index_status is IndexStatus.STALE
    assert current.is_current is True
    assert "$25" in current.content


def test_mutable_issue_snapshot_becomes_valid_when_observed(tmp_path) -> None:
    raw_root = tmp_path / "data" / "raw"
    observed_at = datetime(2026, 7, 13, 8, tzinfo=timezone.utc)
    record = RawWriter(raw_root, "run-001").write_json(
        competitor="github_copilot",
        source_type="github_issue",
        requested_url="https://api.github.com/repos/example/repo/issues/42",
        canonical_url="https://github.com/example/repo/issues/42",
        fetched_at=observed_at,
        payload={
            "kind": "github_issue",
            "repository": "example/repo",
            "item": {
                "number": 42,
                "title": "Agent stalls",
                "body": "The agent stalls on a large repository.",
                "state": "closed",
                "created_at": "2020-01-01T00:00:00Z",
                "updated_at": "2026-07-12T00:00:00Z",
                "html_url": "https://github.com/example/repo/issues/42",
                "labels": [],
                "user": {"login": "reporter"},
            },
            "comments": [
                {
                    "body": "Fixed in the current release.",
                    "created_at": "2026-07-12T00:00:00Z",
                    "user": {"login": "maintainer"},
                }
            ],
        },
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    [document] = _version_rows(result.output_path)
    assert document.publish_time == datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert document.valid_from == observed_at


def test_acquisition_error_is_skipped_without_poisoning_later_replays(
    tmp_path,
    fixture_text,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    valid = RawWriter(raw_root, "run-001").write_bytes(
        competitor="Cursor",
        source_type="official",
        requested_url="https://cursor.com/",
        canonical_url="https://cursor.com/",
        payload=fixture_text("pages/official.html").encode(),
        content_type="text/html",
    )
    RawWriter(raw_root, "run-001").write_error(
        competitor="Cursor",
        source_type="pricing",
        requested_url="https://cursor.com/pricing",
        error="request timed out",
    )

    result = process_raw_records(project_root=tmp_path)

    assert result.succeeded is True
    assert result.partial_failure is False
    assert result.processed_records == 1
    assert result.added == 1
    assert result.skipped == 1
    assert result.failures == []
    [document] = _version_rows(result.output_path)
    assert document.raw_record_id == valid.raw_record_id

    replay = process_raw_records(project_root=tmp_path)
    assert replay.succeeded is True
    assert replay.unchanged == 1
    assert replay.skipped == 1


def test_304_record_is_skipped_without_failure(tmp_path) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-001").write_bytes(
        competitor="Cursor",
        source_type="official",
        requested_url="https://cursor.com/",
        canonical_url="https://cursor.com/",
        payload=None,
        http_status=304,
        content_type="text/html",
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    assert result.succeeded is True
    assert result.skipped == 1
    assert result.processed_records == result.generated_documents == 0
    assert result.output_path.exists()
    assert result.output_path.read_text(encoding="utf-8") == ""


def test_changelog_index_snapshot_is_traceable_but_not_structured(
    tmp_path,
    fixture_text,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-001").write_bytes(
        competitor="cursor",
        source_type="changelog",
        requested_url="https://cursor.com/changelog",
        canonical_url="https://cursor.com/changelog",
        payload=fixture_text("pages/changelog.html").encode(),
        content_type="text/html",
        source_metadata={"record_role": "changelog_index"},
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    assert result.succeeded is True
    assert result.scanned == 1
    assert result.processed_records == 0
    assert result.skipped == 1
    assert result.output_path.read_text(encoding="utf-8") == ""


def test_changelog_candidate_snapshot_is_traceable_but_not_structured(
    tmp_path,
    fixture_text,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-001").write_bytes(
        competitor="tongyi_lingma",
        source_type="changelog",
        requested_url="https://help.aliyun.com/zh/lingma/archive",
        canonical_url="https://help.aliyun.com/zh/lingma/archive",
        payload=fixture_text("pages/aliyun_changelog_detail_old.html").encode(),
        content_type="text/html",
        source_metadata={
            "record_role": "changelog_candidate",
            "candidate_status": "outside_cutoff",
        },
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    assert result.succeeded is True
    assert result.processed_records == 0
    assert result.skipped == 1
    assert result.failures == []
    assert result.output_path.read_text(encoding="utf-8") == ""


def test_configured_directory_changelog_splits_and_filters_dated_sections(
    tmp_path,
    fixture_text,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    url = "https://help.aliyun.com/zh/lingma/qoder-cn-update-log"
    record = RawWriter(raw_root, "run-001").write_bytes(
        competitor="tongyi_lingma",
        source_type="changelog",
        requested_url=url,
        canonical_url=url,
        payload=fixture_text("pages/aliyun_changelog_detail_recent.html").encode(),
        content_type="text/html; charset=utf-8",
        published_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        source_metadata={
            "record_role": "changelog_entry",
            "discovery_mode": "configured_undated_directory",
            "undated_detail_root_selector": (
                ".aliyun-docs-content .markdown-body"
            ),
            "cutoff": "2025-07-13T00:00:00Z",
        },
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    documents = _version_rows(result.output_path)
    assert result.succeeded is True
    assert result.generated_documents == 2
    assert len({document.document_id for document in documents}) == 2
    assert {document.publish_time for document in documents} == {
        datetime(2026, 6, 20, tzinfo=timezone.utc),
        datetime(2026, 7, 10, tzinfo=timezone.utc),
    }
    assert all(document.raw_record_id == record.raw_record_id for document in documents)
    assert all(
        document.source_metadata["date_segmentation"] == "heading"
        for document in documents
    )
    assert not any("2024年12月1日" in document.content for document in documents)


def test_configured_directory_changelog_splits_dated_table_rows(tmp_path) -> None:
    raw_root = tmp_path / "data" / "raw"
    url = "https://help.aliyun.com/zh/lingma/qoder-cn-cli"
    html = """
    <html><head><title>Qoder CN CLI 更新日志</title></head><body>
      <div class="markdown-body"><table>
        <tr><th>日期</th><th>更新内容</th></tr>
        <tr><td>2026-07-10</td><td>新增命令行任务规划。</td></tr>
        <tr><td>2024-12-01</td><td>历史命令行版本。</td></tr>
      </table></div>
    </body></html>
    """
    record = RawWriter(raw_root, "run-table").write_bytes(
        competitor="tongyi_lingma",
        source_type="changelog",
        requested_url=url,
        canonical_url=url,
        payload=html.encode(),
        content_type="text/html; charset=utf-8",
        published_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        source_metadata={
            "record_role": "changelog_entry",
            "discovery_mode": "configured_undated_directory",
            "undated_detail_root_selector": ".markdown-body",
            "cutoff": "2025-07-13T00:00:00Z",
        },
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    [document] = _version_rows(result.output_path)
    assert document.publish_time == datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert document.source_metadata["date_segmentation"] == "table_row"
    assert "新增命令行任务规划" in document.content
    assert "历史命令行版本" not in document.content


def test_dated_table_body_change_creates_version_not_new_document(tmp_path) -> None:
    raw_root = tmp_path / "data" / "raw"
    url = "https://help.aliyun.com/zh/lingma/qoder-cn-cli"

    def payload(body: str) -> bytes:
        return (
            "<html><head><title>Qoder CN CLI 更新日志</title></head><body>"
            '<div class="markdown-body"><table>'
            "<tr><th>日期</th><th>更新内容</th></tr>"
            f"<tr><td>2026-07-10</td><td>{body}</td></tr>"
            "</table></div></body></html>"
        ).encode()

    metadata = {
        "record_role": "changelog_entry",
        "discovery_mode": "configured_undated_directory",
        "undated_detail_root_selector": ".markdown-body",
        "cutoff": "2025-07-13T00:00:00Z",
    }
    first = RawWriter(raw_root, "run-table-v1").write_bytes(
        competitor="tongyi_lingma",
        source_type="changelog",
        requested_url=url,
        canonical_url=url,
        payload=payload("新增命令行任务规划。"),
        content_type="text/html; charset=utf-8",
        fetched_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        source_metadata=metadata,
    )
    second = RawWriter(raw_root, "run-table-v2").write_bytes(
        competitor="tongyi_lingma",
        source_type="changelog",
        requested_url=url,
        canonical_url=url,
        payload=payload("新增命令行任务规划与上下文检查。"),
        content_type="text/html; charset=utf-8",
        fetched_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        source_metadata=metadata,
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [
            _meta_path(raw_root, first.raw_record_id),
            _meta_path(raw_root, second.raw_record_id),
        ]
    )

    documents = _version_rows(result.output_path)
    assert len(documents) == 2
    assert len({document.document_id for document in documents}) == 1
    assert len({document.version_id for document in documents}) == 2
    assert sum(document.is_current for document in documents) == 1
    assert next(document for document in documents if document.is_current).raw_record_id == (
        second.raw_record_id
    )


def test_empty_html_is_reported_without_emitting_document(tmp_path, fixture_text) -> None:
    raw_root = tmp_path / "data" / "raw"
    record = RawWriter(raw_root, "run-001").write_bytes(
        competitor="Cursor",
        source_type="official",
        requested_url="https://cursor.com/empty",
        canonical_url="https://cursor.com/empty",
        payload=fixture_text("pages/empty.html").encode(),
        content_type="text/html",
        needs_browser=True,
    )

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    assert result.processed_records == 0
    assert result.skipped == 1
    assert result.failures == []
    assert result.output_path.read_text(encoding="utf-8") == ""


def test_invalid_json_does_not_block_valid_sibling_record(
    tmp_path,
    fixture_text,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    writer = RawWriter(raw_root, "run-001")
    invalid = writer.write_bytes(
        competitor="GitHub Copilot",
        source_type="github_issue",
        requested_url="https://api.github.com/repos/example/copilot/issues/42",
        canonical_url="https://github.com/example/copilot/issues/42",
        payload=b'{"kind":"github_issue","item":',
        content_type="application/json",
    )
    valid = writer.write_bytes(
        competitor="Cursor",
        source_type="official",
        requested_url="https://cursor.com/",
        canonical_url="https://cursor.com/",
        payload=fixture_text("pages/official.html").encode(),
        content_type="text/html",
    )

    result = ProcessingPipeline(project_root=tmp_path).process()

    assert result.partial_failure is True
    assert result.processed_records == 1
    assert result.added == 1
    assert len(result.failures) == 1
    assert result.failures[0].raw_record_id == invalid.raw_record_id
    [document] = _version_rows(result.output_path)
    assert document.raw_record_id == valid.raw_record_id


def test_rebuild_failure_preserves_existing_cleaned_output(
    tmp_path,
    fixture_text,
) -> None:
    raw_root = tmp_path / "data" / "raw"
    writer = RawWriter(raw_root, "run-001")
    valid = writer.write_bytes(
        competitor="Cursor",
        source_type="official",
        requested_url="https://cursor.com/",
        canonical_url="https://cursor.com/",
        payload=fixture_text("pages/official.html").encode(),
        content_type="text/html",
    )
    pipeline = ProcessingPipeline(project_root=tmp_path)
    first = pipeline.process([_meta_path(raw_root, valid.raw_record_id)])
    before = first.output_path.read_bytes()

    writer.write_bytes(
        competitor="GitHub Copilot",
        source_type="github_issue",
        requested_url="https://api.github.com/repos/example/copilot/issues/42",
        canonical_url="https://github.com/example/copilot/issues/42",
        payload=b'{"kind":"github_issue","item":',
        content_type="application/json",
    )
    rebuilt = pipeline.process(rebuild=True)

    assert rebuilt.partial_failure is True
    assert rebuilt.output_written is False
    assert rebuilt.output_path.read_bytes() == before


def test_payload_hash_mismatch_is_reported_and_not_written(tmp_path, fixture_text) -> None:
    raw_root = tmp_path / "data" / "raw"
    writer = RawWriter(raw_root, "run-001")
    record = writer.write_bytes(
        competitor="Cursor",
        source_type="official",
        requested_url="https://cursor.com/",
        canonical_url="https://cursor.com/",
        payload=fixture_text("pages/official.html").encode(),
        content_type="text/html",
    )
    payload_path = raw_root / record.payload_path
    payload_path.write_text("tampered payload", encoding="utf-8")

    result = ProcessingPipeline(project_root=tmp_path).process(
        [_meta_path(raw_root, record.raw_record_id)]
    )

    assert result.processed_records == 0
    assert len(result.failures) == 1
    assert "SHA-256 mismatch" in result.failures[0].error
    assert result.output_path.read_text(encoding="utf-8") == ""


def _version_rows(path):
    """Load JSONL rows through the public schema for concise assertions."""

    return [
        StructuredDocument.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
