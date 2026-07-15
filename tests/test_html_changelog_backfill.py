from __future__ import annotations

from datetime import datetime, timezone

import requests
import responses

from crawler.crawl_changelog import ChangelogCollector
from crawler.http_client import HttpClient, HttpClientConfig
from crawler.models import CollectorTask
from crawler.storage import RawWriter


def _client() -> HttpClient:
    return HttpClient(
        HttpClientConfig(
            retries=0,
            backoff_factor=0,
            requests_per_second=0,
            trust_environment=False,
            respect_robots_txt=False,
        )
    )


@responses.activate
def test_html_backfill_filters_dates_fetches_details_and_stops_pagination(
    tmp_path,
    fixture_text,
) -> None:
    first = "https://example.test/changelog"
    second = "https://example.test/changelog?page=2"
    alpha = "https://example.test/changelog/alpha"
    beta = "https://example.test/changelog/beta"
    responses.add(
        responses.GET,
        first,
        status=200,
        body=fixture_text("pages/changelog_index_page1.html"),
        content_type="text/html; charset=utf-8",
    )
    responses.add(
        responses.GET,
        alpha,
        status=200,
        body=fixture_text("pages/changelog_detail_alpha.html"),
        content_type="text/html; charset=utf-8",
    )
    responses.add(
        responses.GET,
        second,
        status=200,
        body=fixture_text("pages/changelog_index_page2.html"),
        content_type="text/html; charset=utf-8",
    )
    responses.add(
        responses.GET,
        beta,
        status=200,
        body=fixture_text("pages/changelog_detail_beta.html"),
        content_type="text/html; charset=utf-8",
    )
    writer = RawWriter(tmp_path / "raw", "run-html")

    result = ChangelogCollector(_client(), writer).collect(
        CollectorTask(
            "Cursor",
            "changelog",
            urls=(first,),
            format="html",
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
    )

    assert result.failure_count == 0
    indexes = [
        record
        for record in result.records
        if record.source_metadata["record_role"] == "changelog_index"
    ]
    details = [
        record
        for record in result.records
        if record.source_metadata["record_role"] == "changelog_entry"
    ]
    assert len(indexes) == 2
    assert [record.canonical_url for record in details] == [alpha, beta]
    assert [record.published_at for record in details] == [
        datetime(2026, 7, 12, 9, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 5, tzinfo=timezone.utc),
    ]
    assert indexes[0].source_metadata["eligible_entry_count"] == 1
    assert indexes[1].source_metadata["dated_entry_count"] == 3
    assert indexes[1].source_metadata["eligible_entry_count"] == 2
    assert all(record.needs_browser is False for record in indexes + details)

    called_urls = [call.request.url for call in responses.calls]
    assert called_urls.count(alpha) == 1
    assert "https://example.test/changelog/archived" not in called_urls
    assert "https://example.test/changelog?page=3" not in called_urls


@responses.activate
def test_text_next_link_is_followed(tmp_path, fixture_text) -> None:
    first = "https://example.test/updates"
    second = "https://example.test/updates?page=2"
    detail = "https://example.test/changelog/alpha"
    page_one = """
    <main>
      <article><a href="/changelog/alpha">Alpha</a><time datetime="2026-07-12">July 12, 2026</time></article>
      <a href="/updates?page=2"><span>Next →</span><span>Older posts</span></a>
    </main>
    """
    page_two = """
    <main>
      <p>This page is intentionally older than the cutoff date.</p>
      <article><a href="/changelog/old">Old</a><time datetime="2026-06-01">June 1, 2026</time></article>
    </main>
    """
    responses.add(responses.GET, first, status=200, body=page_one, content_type="text/html")
    responses.add(
        responses.GET,
        detail,
        status=200,
        body=fixture_text("pages/changelog_detail_alpha.html"),
        content_type="text/html",
    )
    responses.add(responses.GET, second, status=200, body=page_two, content_type="text/html")

    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-text-next")
    ).collect(
        CollectorTask(
            "Trae",
            "changelog",
            urls=(first,),
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
    )

    assert result.failure_count == 0
    assert second in [call.request.url for call in responses.calls]
    assert "https://example.test/changelog/old" not in [
        call.request.url for call in responses.calls
    ]


@responses.activate
def test_pinned_old_entry_does_not_stop_pagination(tmp_path, fixture_text) -> None:
    first = "https://example.test/updates"
    second = "https://example.test/updates?page=2"
    new = "https://example.test/changelog/new"
    older_page_new = "https://example.test/changelog/still-in-window"
    page_one = """
    <main>
      <article><a href="/changelog/pinned-old">Pinned</a><time datetime="2025-01-01">January 1, 2025</time></article>
      <article><a href="/changelog/new">New</a><time datetime="2026-07-12">July 12, 2026</time></article>
      <a rel="next" href="/updates?page=2">Next</a>
    </main>
    """
    page_two = """
    <main>
      <article><a href="/changelog/still-in-window">Still in window</a><time datetime="2026-07-05">July 5, 2026</time></article>
    </main>
    """
    responses.add(responses.GET, first, status=200, body=page_one, content_type="text/html")
    responses.add(
        responses.GET,
        new,
        status=200,
        body=fixture_text("pages/changelog_detail_alpha.html"),
        content_type="text/html",
    )
    responses.add(responses.GET, second, status=200, body=page_two, content_type="text/html")
    responses.add(
        responses.GET,
        older_page_new,
        status=200,
        body=fixture_text("pages/changelog_detail_beta.html"),
        content_type="text/html",
    )

    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-pinned-old")
    ).collect(
        CollectorTask(
            "Cursor",
            "changelog",
            urls=(first,),
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
    )

    assert result.failure_count == 0
    called_urls = [call.request.url for call in responses.calls]
    assert second in called_urls
    assert older_page_new in called_urls
    assert "https://example.test/changelog/pinned-old" not in called_urls


@responses.activate
def test_unparseable_html_index_is_preserved_for_browser_audit(
    tmp_path,
    fixture_text,
) -> None:
    url = "https://example.test/dynamic-changelog"
    responses.add(
        responses.GET,
        url,
        status=200,
        body=fixture_text("pages/dynamic.html"),
        content_type="text/html",
    )
    writer = RawWriter(tmp_path / "raw", "run-dynamic")

    result = ChangelogCollector(_client(), writer).collect(
        CollectorTask("Cursor", "changelog", urls=(url,))
    )

    assert result.failure_count == 0
    [record] = result.records
    assert record.payload_path is not None
    assert record.needs_browser is True
    assert record.source_metadata["record_role"] == "changelog_index"
    assert record.source_metadata["parse_warning"] == (
        "no changelog entry links found"
    )


@responses.activate
def test_configured_undated_directory_links_use_target_page_dates(
    tmp_path,
    fixture_text,
) -> None:
    index = "https://help.aliyun.com/zh/lingma/product-overview/dynamics/"
    recent = "https://help.aliyun.com/zh/lingma/qoder-cn-update-log"
    old = "https://help.aliyun.com/zh/lingma/original-tongyi-lingma-logs-archived/"
    responses.add(
        responses.GET,
        index,
        status=200,
        body=fixture_text("pages/aliyun_changelog_directory.html"),
        content_type="text/html; charset=utf-8",
    )
    responses.add(
        responses.GET,
        recent,
        status=200,
        body=fixture_text("pages/aliyun_changelog_detail_recent.html"),
        content_type="text/html; charset=utf-8",
    )
    responses.add(
        responses.GET,
        old,
        status=200,
        body=fixture_text("pages/aliyun_changelog_detail_old.html"),
        content_type="text/html; charset=utf-8",
    )

    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-undated-directory")
    ).collect(
        CollectorTask(
            "tongyi_lingma",
            "changelog",
            urls=(index,),
            since=datetime(2025, 7, 13, tzinfo=timezone.utc),
            metadata={
                "undated_entry_selector": (
                    ".aliyun-docs-content .markdown-body .directory a[href]"
                ),
                "undated_entry_path_prefixes": ["/zh/lingma/"],
                "undated_entry_text_pattern": "更新日志|日志（存档）|产品公告",
                "undated_detail_root_selector": (
                    ".aliyun-docs-content .markdown-body"
                ),
            },
        )
    )

    assert result.failure_count == 0
    index_record = next(
        record
        for record in result.records
        if record.source_metadata["record_role"] == "changelog_index"
    )
    eligible = next(
        record
        for record in result.records
        if record.source_metadata["record_role"] == "changelog_entry"
    )
    undated = next(
        record
        for record in result.records
        if record.source_metadata["record_role"] == "changelog_candidate"
    )
    assert index_record.needs_browser is False
    assert index_record.source_metadata["dated_entry_count"] == 0
    assert index_record.source_metadata["configured_undated_entry_count"] == 2
    assert index_record.source_metadata["undated_entry_status_counts"] == {
        "eligible": 1,
        "undated": 1,
    }
    assert eligible.canonical_url == recent
    assert eligible.published_at == datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert eligible.source_metadata["date_basis"] == "content_latest"
    assert eligible.source_metadata["content_date_count"] == 3
    assert undated.canonical_url == old
    assert undated.source_metadata["candidate_status"] == "undated"
    assert [call.request.url for call in responses.calls] == [index, recent, old]


@responses.activate
def test_html_backfill_rejects_cross_origin_entries_and_pagination(
    tmp_path,
    fixture_text,
) -> None:
    index = "https://example.test/changelog"
    detail = "https://example.test/changelog/safe"
    page = """
    <main>
      <article><a href="/changelog/safe">Safe</a><time datetime="2026-07-12">July 12, 2026</time></article>
      <article><a href="http://127.0.0.1/private">Internal target</a><time datetime="2026-07-11">July 11, 2026</time></article>
      <a rel="next" href="https://unconfigured.example/changelog?page=2">Next</a>
    </main>
    """
    responses.add(responses.GET, index, status=200, body=page, content_type="text/html")
    responses.add(
        responses.GET,
        detail,
        status=200,
        body=fixture_text("pages/changelog_detail_alpha.html"),
        content_type="text/html",
    )

    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-origin")
    ).collect(
        CollectorTask(
            "Cursor",
            "changelog",
            urls=(index,),
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
    )

    assert result.failure_count == 0
    index_record = next(
        record
        for record in result.records
        if record.source_metadata["record_role"] == "changelog_index"
    )
    assert index_record.source_metadata["rejected_cross_origin_entry_count"] == 1
    assert index_record.source_metadata["rejected_cross_origin_next_url"] == (
        "https://unconfigured.example/changelog?page=2"
    )
    assert [call.request.url for call in responses.calls] == [index, detail]


@responses.activate
def test_html_backfill_enforces_total_entry_limit(tmp_path, fixture_text) -> None:
    index = "https://example.test/changelog"
    first = "https://example.test/changelog/first"
    page = """
    <main>
      <article><a href="/changelog/first">First</a><time datetime="2026-07-12">July 12, 2026</time></article>
      <article><a href="/changelog/second">Second</a><time datetime="2026-07-11">July 11, 2026</time></article>
    </main>
    """
    responses.add(responses.GET, index, status=200, body=page, content_type="text/html")
    responses.add(
        responses.GET,
        first,
        status=200,
        body=fixture_text("pages/changelog_detail_alpha.html"),
        content_type="text/html",
    )

    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-limit")
    ).collect(
        CollectorTask(
            "Cursor",
            "changelog",
            urls=(index,),
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
            metadata={"max_entries": 1},
        )
    )

    assert result.failure_count == 0
    details = [
        record
        for record in result.records
        if record.source_metadata["record_role"] == "changelog_entry"
    ]
    assert [record.canonical_url for record in details] == [first]
    assert [call.request.url for call in responses.calls] == [index, first]


@responses.activate
def test_detail_failure_is_isolated_from_sibling_entry(tmp_path, fixture_text) -> None:
    index = "https://example.test/changelog"
    timed_out = "https://example.test/changelog/timed-out"
    healthy = "https://example.test/changelog/healthy"
    responses.add(
        responses.GET,
        index,
        status=200,
        body=fixture_text("pages/changelog_index_failures.html"),
        content_type="text/html",
    )
    responses.add(responses.GET, timed_out, body=requests.Timeout("detail timed out"))
    responses.add(
        responses.GET,
        healthy,
        status=200,
        body=fixture_text("pages/changelog_detail_beta.html"),
        content_type="text/html",
    )

    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-isolation")
    ).collect(
        CollectorTask(
            "Cursor",
            "changelog",
            urls=(index,),
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
    )

    assert result.failure_count == 1
    assert result.errors == [f"{timed_out}: detail timed out"]
    assert any(record.canonical_url == healthy for record in result.records)
    failed = next(record for record in result.records if record.error)
    assert failed.source_metadata["record_role"] == "changelog_entry"


@responses.activate
def test_configured_directory_304_refreshes_index_and_checks_children(
    tmp_path,
    fixture_text,
) -> None:
    index = "https://help.aliyun.com/zh/lingma/product-overview/dynamics/"
    recent = "https://help.aliyun.com/zh/lingma/qoder-cn-update-log"
    old = "https://help.aliyun.com/zh/lingma/original-tongyi-lingma-logs-archived/"
    responses.add(responses.GET, index, status=304)
    responses.add(
        responses.GET,
        index,
        status=200,
        body=fixture_text("pages/aliyun_changelog_directory.html"),
        content_type="text/html; charset=utf-8",
    )
    responses.add(
        responses.GET,
        recent,
        status=200,
        body=fixture_text("pages/aliyun_changelog_detail_recent.html"),
        content_type="text/html; charset=utf-8",
    )
    responses.add(
        responses.GET,
        old,
        status=200,
        body=fixture_text("pages/aliyun_changelog_detail_old.html"),
        content_type="text/html; charset=utf-8",
    )

    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-directory-304")
    ).collect(
        CollectorTask(
            "tongyi_lingma",
            "changelog",
            urls=(index,),
            since=datetime(2025, 7, 13, tzinfo=timezone.utc),
            metadata={
                "undated_entry_selector": (
                    ".aliyun-docs-content .markdown-body .directory a[href]"
                ),
                "undated_entry_path_prefixes": ["/zh/lingma/"],
                "undated_entry_text_pattern": "更新日志|日志（存档）|产品公告",
                "undated_detail_root_selector": (
                    ".aliyun-docs-content .markdown-body"
                ),
            },
        )
    )

    assert result.failure_count == 0
    assert [call.request.url for call in responses.calls] == [index, index, recent, old]
    indexes = [
        record
        for record in result.records
        if record.source_metadata["record_role"] == "changelog_index"
    ]
    assert [record.http_status for record in indexes] == [304, 200]
    assert indexes[0].source_metadata["directory_refresh_required"] is True
    assert indexes[1].source_metadata["directory_refresh_after_304"] is True
    assert any(
        record.source_metadata.get("record_role") == "changelog_entry"
        for record in result.records
    )


@responses.activate
def test_index_cross_origin_redirect_is_rejected_before_discovery(tmp_path) -> None:
    index = "https://example.test/changelog"
    redirected = "https://outside.test/changelog"
    responses.add(
        responses.GET,
        index,
        status=302,
        headers={"Location": redirected},
    )
    responses.add(
        responses.GET,
        redirected,
        status=200,
        body=(
            "<html><body><a href='/2026-07-01'>2026-07-01 release</a>"
            "</body></html>"
        ),
        content_type="text/html",
    )

    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-index-redirect")
    ).collect(CollectorTask("Cursor", "changelog", urls=(index,)))

    assert result.failure_count == 1
    assert not any(
        record.source_metadata.get("record_role") == "changelog_entry"
        for record in result.records
    )
    [failed] = [record for record in result.records if record.error]
    assert failed.payload_path is None
    assert failed.source_metadata["redirect_rejected"] is True


@responses.activate
def test_configured_detail_redirect_outside_path_scope_is_rejected(tmp_path) -> None:
    index = "https://help.aliyun.com/zh/lingma/product-overview/dynamics/"
    alias = "https://help.aliyun.com/zh/lingma/qoder-cn-update-log"
    login = "https://help.aliyun.com/account/login"
    directory = """
    <html><body><section class="aliyun-docs-content"><div class="markdown-body">
      <p>该目录提供产品发布记录、版本说明和公告，正文长度足以执行静态入口检查。</p>
      <div class="directory"><a href="/zh/lingma/qoder-cn-update-log">Qoder CN 更新日志</a></div>
    </div></section></body></html>
    """
    responses.add(
        responses.GET,
        index,
        status=200,
        body=directory,
        content_type="text/html",
    )
    responses.add(
        responses.GET,
        alias,
        status=302,
        headers={"Location": login},
    )
    responses.add(
        responses.GET,
        login,
        status=200,
        body="<html><body><h1>Login</h1></body></html>",
        content_type="text/html",
    )

    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-detail-redirect")
    ).collect(
        CollectorTask(
            "tongyi_lingma",
            "changelog",
            urls=(index,),
            metadata={
                "undated_entry_selector": ".directory a[href]",
                "undated_entry_path_prefixes": ["/zh/lingma/"],
                "undated_entry_text_pattern": "更新日志",
                "undated_detail_root_selector": ".markdown-body",
            },
        )
    )

    assert result.failure_count == 1
    assert not any(
        record.source_metadata.get("record_role") == "changelog_entry"
        for record in result.records
    )
    failed = next(record for record in result.records if record.error)
    assert failed.payload_path is None
    assert failed.source_metadata["candidate_status"] == "redirect_out_of_scope"


@responses.activate
def test_time_only_card_is_candidate_when_processor_has_no_date_boundary(tmp_path) -> None:
    index = "https://help.aliyun.com/zh/lingma/product-overview/dynamics/"
    detail = "https://help.aliyun.com/zh/lingma/qoder-cn-update-log"
    directory = """
    <html><body><div class="directory">
      <a href="/zh/lingma/qoder-cn-update-log">Qoder CN 更新日志</a>
    </div></body></html>
    """
    card_page = """
    <html><body><div class="markdown-body"><article>
      <time datetime="2026-07-10">2026-07-10</time>
      <h2>新增任务规划</h2><p>改进项目级上下文处理。</p>
    </article></div></body></html>
    """
    responses.add(
        responses.GET,
        index,
        status=200,
        body=directory,
        content_type="text/html",
    )
    responses.add(
        responses.GET,
        detail,
        status=200,
        body=card_page,
        content_type="text/html",
    )

    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-time-card")
    ).collect(
        CollectorTask(
            "tongyi_lingma",
            "changelog",
            urls=(index,),
            metadata={
                "undated_entry_selector": ".directory a[href]",
                "undated_entry_path_prefixes": ["/zh/lingma/"],
                "undated_entry_text_pattern": "更新日志",
                "undated_detail_root_selector": ".markdown-body",
            },
        )
    )

    assert result.failure_count == 0
    candidate = next(
        record
        for record in result.records
        if record.source_metadata.get("record_role") == "changelog_candidate"
    )
    assert candidate.source_metadata["candidate_status"] == "undated"
    assert candidate.source_metadata["content_date_count"] == 0
