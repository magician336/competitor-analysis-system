from __future__ import annotations

import responses

from crawler.base import ConfiguredPageCollector
from crawler.browser_renderer import RenderedPage
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


def _page(title: str, *links: str) -> str:
    anchors = "".join(f'<a href="{link}">{link}</a>' for link in links)
    return (
        f"<html><head><title>{title}</title></head><body><main><h1>{title}</h1>"
        "<p>This official documentation page contains enough visible text for "
        "bounded acquisition and evidence processing.</p>"
        f"{anchors}</main></body></html>"
    )


class _ExternalRedirectBrowserRenderer:
    def render(self, url: str, **_kwargs: object) -> RenderedPage:
        return RenderedPage(
            html=_page("External browser page", "/docs/child"),
            final_url="https://outside.test/docs",
            http_status=200,
        )

    def close(self) -> None:
        return None


@responses.activate
def test_configured_page_collector_recursively_discovers_bounded_links(
    tmp_path,
) -> None:
    seed = "https://example.test/docs"
    first = "https://example.test/docs/a"
    second = "https://example.test/docs/b"
    responses.add(responses.GET, seed, body=_page("Seed", "/docs/a"), status=200)
    responses.add(
        responses.GET,
        first,
        body=_page("First", "/docs/b", "/docs/c", "/outside"),
        status=200,
    )
    responses.add(responses.GET, second, body=_page("Second"), status=200)
    task = CollectorTask(
        "cursor",
        "product_docs",
        urls=(seed,),
        metadata={
            "evidence_level": "A",
            "link_discovery": {
                "enabled": True,
                "allowed_path_prefixes": ["/docs"],
                "max_depth": 2,
                "max_urls": 2,
            },
        },
    )

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-links"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)

    assert result.failure_count == 0
    assert [record.requested_url for record in result.records] == [
        seed,
        first,
        second,
    ]
    assert "discovery" not in result.records[0].source_metadata
    assert result.records[1].source_metadata["discovery"] == {
        "method": "same_origin_links",
        "depth": 1,
        "max_depth": 2,
    }
    assert result.records[2].source_metadata["discovery"]["depth"] == 2
    assert len(responses.calls) == 3


@responses.activate
def test_invalid_link_discovery_does_not_block_configured_seed(tmp_path) -> None:
    seed = "https://example.test/docs"
    responses.add(responses.GET, seed, body=_page("Seed"), status=200)
    task = CollectorTask(
        "cursor",
        "product_docs",
        urls=(seed,),
        metadata={
            "link_discovery": {
                "enabled": True,
                "max_depth": 1,
                "max_urls": 10,
            }
        },
    )

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-invalid-links"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)

    assert result.success_count == 1
    assert result.failure_count == 1
    assert result.records[0].requested_url == seed
    assert result.errors == [
        "enabled link_discovery requires allowed_path_prefixes"
    ]


@responses.activate
def test_link_discovery_rejects_seed_redirect_outside_configured_origins(
    tmp_path,
) -> None:
    seed = "https://allowed.test/docs"
    redirected = "https://outside.test/docs"
    outside_child = "https://outside.test/docs/child"
    responses.add(
        responses.GET,
        seed,
        status=302,
        headers={"Location": redirected},
    )
    responses.add(
        responses.GET,
        redirected,
        body=_page("Redirected", "/docs/child"),
        status=200,
    )
    responses.add(
        responses.GET,
        outside_child,
        body=_page("Outside child"),
        status=200,
    )
    task = CollectorTask(
        "cursor",
        "product_docs",
        urls=(seed,),
        metadata={
            "link_discovery": {
                "enabled": True,
                "allowed_path_prefixes": ["/docs"],
                "max_depth": 2,
                "max_urls": 10,
            }
        },
    )

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-redirect"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)

    assert result.success_count == 0
    assert result.failure_count == 1
    assert len(result.records) == 1
    assert result.records[0].requested_url == seed
    assert result.records[0].final_url is None
    assert result.records[0].payload_path is None
    assert "redirect target is outside configured" in (
        result.records[0].error or ""
    )
    assert [call.request.url for call in responses.calls] == [seed]


@responses.activate
def test_link_discovery_follows_same_origin_redirect_and_collects_page(
    tmp_path,
) -> None:
    seed = "https://example.test/docs"
    redirected = "https://example.test/docs/start"
    child = "https://example.test/docs/child"
    responses.add(
        responses.GET,
        seed,
        status=302,
        headers={"Location": "/docs/start"},
    )
    responses.add(
        responses.GET,
        redirected,
        body=_page("Redirected seed", "/docs/child"),
        status=200,
    )
    responses.add(
        responses.GET,
        child,
        body=_page("Child"),
        status=200,
    )
    task = CollectorTask(
        "cursor",
        "product_docs",
        urls=(seed,),
        metadata={
            "link_discovery": {
                "enabled": True,
                "allowed_path_prefixes": ["/docs"],
                "max_depth": 1,
                "max_urls": 10,
            }
        },
    )

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-same-origin-redirect"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)

    assert result.failure_count == 0
    assert [record.requested_url for record in result.records] == [seed, child]
    assert result.records[0].final_url == redirected
    assert [call.request.url for call in responses.calls] == [
        seed,
        redirected,
        child,
    ]


@responses.activate
def test_link_discovery_stops_after_five_same_origin_redirects(tmp_path) -> None:
    urls = [
        f"https://example.test/docs/redirect-{index}"
        for index in range(7)
    ]
    for current, following in zip(urls, urls[1:]):
        responses.add(
            responses.GET,
            current,
            status=302,
            headers={"Location": following},
        )
    responses.add(
        responses.GET,
        urls[-1],
        body=_page("Unreachable sixth target"),
        status=200,
    )
    task = CollectorTask(
        "cursor",
        "product_docs",
        urls=(urls[0],),
        metadata={
            "link_discovery": {
                "enabled": True,
                "allowed_path_prefixes": ["/docs"],
                "max_depth": 1,
                "max_urls": 10,
            }
        },
    )

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-redirect-cap"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)

    assert result.success_count == 0
    assert result.failure_count == 1
    assert "maximum of 5 same-origin redirects" in (
        result.records[0].error or ""
    )
    assert [call.request.url for call in responses.calls] == urls[:6]


@responses.activate
def test_link_discovery_rejects_browser_navigation_outside_seed_origins(
    tmp_path,
) -> None:
    seed = "https://allowed.test/docs"
    responses.add(
        responses.GET,
        seed,
        body="<html><body><main></main></body></html>",
        status=200,
        content_type="text/html",
    )
    task = CollectorTask(
        "cursor",
        "product_docs",
        urls=(seed,),
        metadata={
            "browser_fallback": {
                "enabled": True,
                "minimum_text_characters": 100,
            },
            "link_discovery": {
                "enabled": True,
                "allowed_path_prefixes": ["/docs"],
                "max_depth": 1,
                "max_urls": 10,
            },
        },
    )

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-browser-redirect"),
        SourceType.PRODUCT_DOCS,
        browser_renderer=_ExternalRedirectBrowserRenderer(),
    ).collect(task)

    assert result.success_count == 0
    assert result.failure_count == 1
    [record] = result.records
    assert record.payload_path is None
    assert record.final_url == "https://outside.test/docs"
    assert "browser rendering left configured" in (record.error or "")


@responses.activate
def test_http_304_uses_verified_historical_payload_for_discovery(tmp_path) -> None:
    seed = "https://example.test/docs"
    child = "https://example.test/docs/child"
    responses.add(
        responses.GET,
        seed,
        body=_page("Seed", "/docs/child"),
        status=200,
        headers={"ETag": '"seed-v1"'},
    )
    responses.add(
        responses.GET,
        child,
        body=_page("Child v1"),
        status=200,
    )
    responses.add(
        responses.GET,
        seed,
        status=304,
        headers={"ETag": '"seed-v1"'},
    )
    responses.add(
        responses.GET,
        child,
        body=_page("Child v2"),
        status=200,
    )
    raw_root = tmp_path / "data" / "raw"
    client = _client()
    task = CollectorTask(
        "cursor",
        "product_docs",
        urls=(seed,),
        metadata={
            "link_discovery": {
                "enabled": True,
                "allowed_path_prefixes": ["/docs"],
                "max_depth": 1,
                "max_urls": 10,
            }
        },
    )

    first = ConfiguredPageCollector(
        client,
        RawWriter(raw_root, "run-first"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)
    second_call_start = len(responses.calls)
    second = ConfiguredPageCollector(
        client,
        RawWriter(raw_root, "run-second"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)

    assert first.failure_count == 0
    assert second.failure_count == 0
    assert second.unchanged_count == 1
    assert [record.requested_url for record in second.records] == [seed, child]
    assert [call.request.url for call in responses.calls[second_call_start:]] == [
        seed,
        child,
    ]


@responses.activate
def test_http_304_without_history_stops_discovery_with_explicit_error(
    tmp_path,
) -> None:
    seed = "https://example.test/docs"
    responses.add(responses.GET, seed, status=304)
    task = CollectorTask(
        "cursor",
        "product_docs",
        urls=(seed,),
        metadata={
            "link_discovery": {
                "enabled": True,
                "allowed_path_prefixes": ["/docs"],
                "max_depth": 1,
                "max_urls": 10,
            }
        },
    )

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-no-history"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)

    assert result.success_count == 0
    assert result.unchanged_count == 1
    assert result.failure_count == 1
    assert result.errors == [
        f"{seed}: link discovery stopped after HTTP 304: "
        "no verified historical raw payload is available"
    ]
    assert len(responses.calls) == 1


@responses.activate
def test_http_304_rejects_corrupted_historical_payload(tmp_path) -> None:
    seed = "https://example.test/docs"
    responses.add(
        responses.GET,
        seed,
        body=_page("Seed", "/docs/child"),
        status=200,
        headers={"ETag": '"seed-v1"'},
    )
    responses.add(
        responses.GET,
        "https://example.test/docs/child",
        body=_page("Child"),
        status=200,
    )
    responses.add(responses.GET, seed, status=304)
    raw_root = tmp_path / "data" / "raw"
    client = _client()
    task = CollectorTask(
        "cursor",
        "product_docs",
        urls=(seed,),
        metadata={
            "link_discovery": {
                "enabled": True,
                "allowed_path_prefixes": ["/docs"],
                "max_depth": 1,
                "max_urls": 10,
            }
        },
    )
    first = ConfiguredPageCollector(
        client,
        RawWriter(raw_root, "run-first"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)
    seed_record = next(
        record for record in first.records if record.requested_url == seed
    )
    payload_path = (raw_root / str(seed_record.payload_path)).resolve()
    assert payload_path.is_relative_to(raw_root.resolve())
    payload_path.write_bytes(b"corrupted")

    second = ConfiguredPageCollector(
        client,
        RawWriter(raw_root, "run-second"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)

    assert second.success_count == 0
    assert second.unchanged_count == 1
    assert second.failure_count == 1
    assert "no verified historical raw payload" in second.errors[0]


@responses.activate
def test_seed_and_discovered_links_share_canonical_identity(tmp_path) -> None:
    seed = "https://example.test/docs?utm_source=config"
    responses.add(
        responses.GET,
        seed,
        body=_page("Seed", "/docs"),
        status=200,
    )
    task = CollectorTask(
        "cursor",
        "product_docs",
        urls=(seed,),
        metadata={
            "link_discovery": {
                "enabled": True,
                "allowed_path_prefixes": ["/docs"],
                "max_depth": 1,
                "max_urls": 10,
            }
        },
    )

    result = ConfiguredPageCollector(
        _client(),
        RawWriter(tmp_path / "data" / "raw", "run-canonical-seed"),
        SourceType.PRODUCT_DOCS,
    ).collect(task)

    assert result.failure_count == 0
    assert [record.requested_url for record in result.records] == [seed]
    assert len(responses.calls) == 1
