from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import requests
import responses

from crawler.browser_renderer import BrowserRenderError, RenderedPage
from crawler.crawl_changelog import ChangelogCollector
from crawler.crawl_github import GitHubCollector
from crawler.crawl_official import OfficialCollector
from crawler.crawl_pricing import PricingCollector
from crawler.http_client import HttpClient, HttpClientConfig
from crawler.models import CollectorTask, SourceType
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


def _payload(writer: RawWriter, record) -> bytes:
    return (writer.raw_root / record.payload_path).read_bytes()


class _FakeBrowserRenderer:
    def __init__(self, html: str) -> None:
        self.html = html
        self.calls: list[tuple[str, dict[str, object]]] = []

    def render(self, url: str, **kwargs) -> RenderedPage:
        self.calls.append((url, kwargs))
        return RenderedPage(
            html=self.html,
            final_url=url,
            http_status=200,
        )

    def close(self) -> None:
        return None


class _FailingBrowserRenderer:
    def render(self, url: str, **kwargs) -> RenderedPage:
        raise BrowserRenderError("browser fixture failed")

    def close(self) -> None:
        return None


@responses.activate
def test_official_collector_persists_200_snapshot(tmp_path, fixture_text) -> None:
    url = "https://example.test/"
    responses.add(
        responses.GET,
        url,
        status=200,
        body=fixture_text("pages/official.html"),
        content_type="text/html; charset=utf-8",
        headers={"ETag": '"official-1"'},
    )
    writer = RawWriter(tmp_path / "raw", "run-001")
    collector = OfficialCollector(_client(), writer)

    result = collector.collect(
        CollectorTask("Cursor", "official", urls=(url,), metadata={"evidence_level": "A"})
    )

    assert result.success_count == 1
    assert result.failure_count == 0
    [record] = result.records
    assert record.source_type is SourceType.OFFICIAL
    assert record.http_status == 200
    assert record.etag == '"official-1"'
    assert record.needs_browser is False
    assert _payload(writer, record).startswith(b"<!doctype html>")


@responses.activate
def test_dynamic_page_is_preserved_and_marked_for_browser(tmp_path, fixture_text) -> None:
    url = "https://example.test/dynamic"
    responses.add(
        responses.GET,
        url,
        status=200,
        body=fixture_text("pages/dynamic.html"),
        content_type="text/html",
    )
    writer = RawWriter(tmp_path / "raw", "run-001")

    result = PricingCollector(_client(), writer).collect(
        CollectorTask("Cursor", "pricing", urls=(url,))
    )

    [record] = result.records
    assert record.needs_browser is True
    assert record.source_metadata["visible_text_length"] < 80
    assert _payload(writer, record)


@responses.activate
def test_empty_html_is_preserved_and_marked_for_browser(tmp_path, fixture_text) -> None:
    url = "https://example.test/empty"
    responses.add(
        responses.GET,
        url,
        status=200,
        body=fixture_text("pages/empty.html"),
        content_type="text/html",
    )
    writer = RawWriter(tmp_path / "raw", "run-001")

    result = OfficialCollector(_client(), writer).collect(
        CollectorTask("Cursor", "official", urls=(url,))
    )

    assert result.failure_count == 0
    [record] = result.records
    assert record.needs_browser is True
    assert record.source_metadata["visible_text_length"] < 80


@responses.activate
def test_configured_browser_fallback_persists_rendered_html(tmp_path) -> None:
    url = "https://example.test/dynamic-home"
    responses.add(
        responses.GET,
        url,
        status=200,
        body="<html><body><div id='root'></div></body></html>",
        content_type="text/html",
    )
    rendered_html = """
    <html><body><div id="root"><main>
      <h1>Rendered product homepage</h1>
      <p>This browser-rendered product description contains enough stable
      text to pass the configured acquisition threshold and be processed.</p>
    </main></div></body></html>
    """
    renderer = _FakeBrowserRenderer(rendered_html)
    writer = RawWriter(tmp_path / "raw", "run-browser")
    collector = OfficialCollector(
        _client(),
        writer,
        browser_renderer=renderer,
    )

    result = collector.collect(
        CollectorTask(
            "Trae",
            "official",
            urls=(url,),
            metadata={
                "browser_fallback": {
                    "enabled": True,
                    "wait_selector": "#root",
                    "minimum_text_characters": 100,
                    "timeout_seconds": 20,
                    "settle_milliseconds": 500,
                }
            },
        )
    )

    [record] = result.records
    assert record.needs_browser is False
    assert record.source_metadata["browser_rendered"] is True
    assert record.source_metadata["browser_static_visible_text_length"] == 0
    assert record.source_metadata["browser_rendered_visible_text_length"] > 80
    assert b"Rendered product homepage" in _payload(writer, record)
    assert renderer.calls[0][0] == url
    assert renderer.calls[0][1]["wait_selector"] == "#root"


@responses.activate
def test_browser_fallback_failure_preserves_static_response(tmp_path) -> None:
    url = "https://example.test/dynamic-home"
    static_html = "<html><body><div id='root'></div></body></html>"
    responses.add(
        responses.GET,
        url,
        status=200,
        body=static_html,
        content_type="text/html",
    )
    writer = RawWriter(tmp_path / "raw", "run-browser-failure")
    collector = OfficialCollector(
        _client(),
        writer,
        browser_renderer=_FailingBrowserRenderer(),
    )

    result = collector.collect(
        CollectorTask(
            "CodeGeeX",
            "official",
            urls=(url,),
            metadata={"browser_fallback": {"enabled": True}},
        )
    )

    [record] = result.records
    assert result.failure_count == 0
    assert record.needs_browser is True
    assert record.source_metadata["browser_fallback_error"] == (
        "browser fixture failed"
    )
    assert _payload(writer, record) == static_html.encode()


@responses.activate
def test_page_collector_records_304_without_payload(tmp_path) -> None:
    url = "https://example.test/pricing"
    responses.add(responses.GET, url, status=304, headers={"ETag": '"v1"'})
    writer = RawWriter(tmp_path / "raw", "run-001")

    result = PricingCollector(_client(), writer).collect(
        CollectorTask("Cursor", "pricing", urls=(url,))
    )

    assert result.unchanged_count == 1
    [record] = result.records
    assert record.http_status == 304
    assert record.payload_path is None


@responses.activate
def test_one_page_timeout_does_not_block_sibling_url(tmp_path, fixture_text) -> None:
    slow = "https://example.test/slow"
    healthy = "https://example.test/healthy"
    responses.add(responses.GET, slow, body=requests.Timeout("timed out"))
    responses.add(
        responses.GET,
        healthy,
        status=200,
        body=fixture_text("pages/official.html"),
        content_type="text/html",
    )
    writer = RawWriter(tmp_path / "raw", "run-001")

    result = OfficialCollector(_client(), writer).collect(
        CollectorTask("Cursor", "official", urls=(slow, healthy))
    )

    assert result.success_count == 1
    assert result.failure_count == 1
    assert any(record.error == "timed out" for record in result.records)
    assert any(record.http_status == 200 for record in result.records)


@responses.activate
def test_rss_changelog_collector_materializes_entry_json(tmp_path, fixture_text) -> None:
    url = "https://example.test/changelog.xml"
    responses.add(
        responses.GET,
        url,
        status=200,
        body=fixture_text("feeds/changelog.xml"),
        content_type="application/rss+xml",
    )
    writer = RawWriter(tmp_path / "raw", "run-001")
    collector = ChangelogCollector(_client(), writer)

    result = collector.collect(
        CollectorTask(
            "GitHub Copilot",
            "changelog",
            urls=(url,),
            format="rss",
            since=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
    )

    assert result.success_count == 1
    [record] = result.records
    envelope = json.loads(_payload(writer, record))
    assert record.source_type is SourceType.CHANGELOG
    assert record.published_at == datetime(2026, 7, 1, 8, 30, tzinfo=timezone.utc)
    assert envelope["kind"] == "changelog_entry"
    assert envelope["entry"]["title"] == "Copilot coding agent update"
    assert record.source_metadata["format"] == "feed_entry"


@responses.activate
def test_rss_changelog_respects_since_cutoff(tmp_path, fixture_text) -> None:
    url = "https://example.test/changelog.xml"
    responses.add(
        responses.GET,
        url,
        status=200,
        body=fixture_text("feeds/changelog.xml"),
        content_type="application/rss+xml",
    )
    result = ChangelogCollector(
        _client(), RawWriter(tmp_path / "raw", "run-001")
    ).collect(
        CollectorTask(
            "GitHub Copilot",
            "changelog",
            urls=(url,),
            format="rss",
            since=datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
    )

    assert result.records == []
    assert result.failure_count == 0


@responses.activate
def test_malformed_rss_does_not_block_next_feed_url(tmp_path, fixture_text) -> None:
    malformed_url = "https://example.test/malformed.xml"
    healthy_url = "https://example.test/changelog.xml"
    responses.add(
        responses.GET,
        malformed_url,
        status=200,
        body="this payload is not an RSS or Atom document",
        content_type="application/rss+xml",
    )
    responses.add(
        responses.GET,
        healthy_url,
        status=200,
        body=fixture_text("feeds/changelog.xml"),
        content_type="application/rss+xml",
    )
    writer = RawWriter(tmp_path / "raw", "run-001")

    result = ChangelogCollector(_client(), writer).collect(
        CollectorTask(
            "GitHub Copilot",
            "changelog",
            urls=(malformed_url, healthy_url),
            format="rss",
        )
    )

    assert result.failure_count == 0
    assert len(result.records) == 2
    malformed, healthy = result.records
    assert malformed.source_metadata["parse_warning"] == "no feed entries found"
    assert malformed.source_metadata["entry_count"] == 0
    assert json.loads(_payload(writer, healthy))["kind"] == "changelog_entry"


@responses.activate
def test_github_releases_follow_link_pagination_and_send_read_token(
    tmp_path,
    fixture_json,
) -> None:
    base = "https://api.github.com/repos/example/copilot/releases"
    page_two = f"{base}?page=2"
    responses.add(
        responses.GET,
        re.compile(r"https://api\.github\.com/repos/example/copilot/releases\?.*"),
        status=200,
        json=fixture_json("github/releases_page1.json"),
        headers={
            "Link": f'<{page_two}>; rel="next"',
            "ETag": '"release-page-1"',
            "Last-Modified": "Wed, 01 Jul 2026 08:30:00 GMT",
        },
    )
    responses.add(
        responses.GET,
        page_two,
        status=200,
        json=fixture_json("github/releases_page2.json"),
    )
    writer = RawWriter(tmp_path / "raw", "run-001")
    collector = GitHubCollector(_client(), writer, token="read-token")

    result = collector.collect(
        CollectorTask(
            "GitHub Copilot",
            "github",
            repositories=("example/copilot",),
            metadata={"collect_issues": False},
        )
    )

    assert result.success_count == 2
    assert [record.source_type for record in result.records] == [
        SourceType.GITHUB_RELEASE,
        SourceType.GITHUB_RELEASE,
    ]
    assert result.records[0].etag == '"release-page-1"'
    assert result.records[0].last_modified == "Wed, 01 Jul 2026 08:30:00 GMT"
    assert all(
        call.request.headers["Authorization"] == "Bearer read-token"
        for call in responses.calls
    )
    envelopes = [json.loads(_payload(writer, record)) for record in result.records]
    assert [item["item"]["tag_name"] for item in envelopes] == ["v1.2.0", "v1.1.0"]


@responses.activate
def test_github_issues_paginate_exclude_pull_requests_and_collect_comments(
    tmp_path,
    fixture_json,
) -> None:
    base = "https://api.github.com/repos/example/copilot/issues"
    page_two = f"{base}?page=2"
    responses.add(
        responses.GET,
        re.compile(r"https://api\.github\.com/repos/example/copilot/issues\?.*"),
        status=200,
        json=fixture_json("github/issues_page1.json"),
        headers={
            "Link": f'<{page_two}>; rel="next"',
            "ETag": '"issue-page-1"',
            "Last-Modified": "Wed, 01 Jul 2026 09:00:00 GMT",
        },
    )
    responses.add(
        responses.GET,
        re.compile(r"https://api\.github\.com/repos/example/copilot/issues/42/comments\?.*"),
        status=200,
        json=fixture_json("github/issue_42_comments.json"),
    )
    responses.add(
        responses.GET,
        page_two,
        status=200,
        json=fixture_json("github/issues_page2.json"),
    )
    responses.add(
        responses.GET,
        re.compile(r"https://api\.github\.com/repos/example/copilot/issues/44/comments\?.*"),
        status=200,
        json=[],
    )
    writer = RawWriter(tmp_path / "raw", "run-001")
    collector = GitHubCollector(_client(), writer)

    result = collector.collect(
        CollectorTask(
            "GitHub Copilot",
            "github",
            repositories=("example/copilot",),
            max_issues=2,
            max_comments=1,
            metadata={"collect_releases": False},
        )
    )

    assert result.failure_count == 0
    assert result.success_count == 2
    assert result.records[0].etag == '"issue-page-1"'
    assert result.records[0].last_modified == "Wed, 01 Jul 2026 09:00:00 GMT"
    envelopes = [json.loads(_payload(writer, record)) for record in result.records]
    assert [item["item"]["number"] for item in envelopes] == [42, 44]
    assert envelopes[0]["comments"][0]["id"] == 501
    assert envelopes[1]["comments"] == []
    assert all("pull_request" not in item["item"] for item in envelopes)
    assert not any("/issues/44/comments" in call.request.url for call in responses.calls)


@responses.activate
def test_github_comment_failure_is_reported_without_dropping_issue(
    tmp_path,
    fixture_json,
) -> None:
    responses.add(
        responses.GET,
        re.compile(r"https://api\.github\.com/repos/example/copilot/issues\?.*"),
        status=200,
        json=fixture_json("github/issues_page1.json"),
    )
    responses.add(
        responses.GET,
        re.compile(
            r"https://api\.github\.com/repos/example/copilot/issues/42/comments\?.*"
        ),
        status=503,
        json={"message": "temporarily unavailable"},
    )
    writer = RawWriter(tmp_path / "raw", "run-001")

    result = GitHubCollector(_client(), writer).collect(
        CollectorTask(
            "GitHub Copilot",
            "github",
            repositories=("example/copilot",),
            max_issues=1,
            max_comments=1,
            metadata={"collect_releases": False},
        )
    )

    assert result.success_count == 1
    assert result.failure_count == 1
    assert result.errors == ["example/copilot#42 comments: HTTP 503"]
    envelope = json.loads(_payload(writer, result.records[0]))
    assert envelope["item"]["number"] == 42
    assert envelope["comments"] == []
    assert envelope["comments_error"] == "HTTP 503"


@responses.activate
def test_github_rate_limit_error_is_auditable_and_does_not_raise(tmp_path) -> None:
    url = re.compile(r"https://api\.github\.com/repos/example/copilot/releases\?.*")
    responses.add(
        responses.GET,
        url,
        status=403,
        json={"message": "API rate limit exceeded"},
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "12345"},
    )
    writer = RawWriter(tmp_path / "raw", "run-001")

    result = GitHubCollector(_client(), writer).collect(
        CollectorTask(
            "GitHub Copilot",
            "github",
            repositories=("example/copilot",),
            metadata={"collect_issues": False},
        )
    )

    assert result.failure_count == 1
    assert "rate limit exhausted" in result.errors[0]
    assert result.records[0].http_status == 403
    assert result.records[0].error and "reset=12345" in result.records[0].error
