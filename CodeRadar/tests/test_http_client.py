from __future__ import annotations

import json

import pytest
import requests
import responses

from crawler.http_client import (
    ConditionalRequestStore,
    DomainRateLimiter,
    HttpClient,
    HttpClientConfig,
    RobotsDeniedError,
)


def _client(**overrides) -> HttpClient:
    config = HttpClientConfig(
        retries=overrides.pop("retries", 0),
        backoff_factor=0,
        requests_per_second=0,
        trust_environment=False,
        respect_robots_txt=overrides.pop("respect_robots_txt", False),
        **overrides,
    )
    return HttpClient(config)


@responses.activate
def test_successful_get_records_conditional_headers_and_sends_them_next_time(
    tmp_path,
) -> None:
    url = "https://example.test/changelog?b=2&a=1"
    cache_path = tmp_path / "conditional.json"
    store = ConditionalRequestStore(cache_path)
    client = HttpClient(
        HttpClientConfig(
            retries=0,
            requests_per_second=0,
            trust_environment=False,
            respect_robots_txt=False,
        ),
        condition_store=store,
    )
    responses.add(
        responses.GET,
        url,
        body="first version",
        status=200,
        headers={
            "ETag": '"version-1"',
            "Last-Modified": "Wed, 01 Jul 2026 08:30:00 GMT",
        },
    )

    first = client.get(url)
    assert first.status_code == 200
    assert cache_path.exists()
    assert store.get_headers("https://example.test/changelog?a=1&b=2") == {
        "If-None-Match": '"version-1"',
        "If-Modified-Since": "Wed, 01 Jul 2026 08:30:00 GMT",
    }

    def unchanged(request):
        assert request.headers["If-None-Match"] == '"version-1"'
        assert request.headers["If-Modified-Since"] == (
            "Wed, 01 Jul 2026 08:30:00 GMT"
        )
        return 304, {}, ""

    responses.add_callback(responses.GET, url, callback=unchanged)
    second = client.get(url)
    assert second.status_code == 304


@responses.activate
def test_force_disables_conditional_request_headers(tmp_path) -> None:
    url = "https://example.test/pricing"
    store = ConditionalRequestStore(tmp_path / "conditional.json")
    store.update(url, {"ETag": '"cached"'})
    client = HttpClient(
        HttpClientConfig(
            retries=0,
            requests_per_second=0,
            trust_environment=False,
            respect_robots_txt=False,
        ),
        condition_store=store,
    )

    def callback(request):
        assert "If-None-Match" not in request.headers
        return 200, {"Content-Type": "text/html"}, "fresh"

    responses.add_callback(responses.GET, url, callback=callback)
    assert client.get(url, force=True).text == "fresh"


@responses.activate
def test_http_client_retries_429_and_returns_following_success() -> None:
    url = "https://api.example.test/items"
    client = _client(retries=1)
    responses.add(
        responses.GET,
        url,
        status=429,
        headers={"Retry-After": "0"},
    )
    responses.add(
        responses.GET,
        url,
        status=200,
        json={"ok": True},
    )

    response = client.get(url)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(responses.calls) == 2


@responses.activate
def test_http_client_surfaces_timeout_after_retry_budget() -> None:
    url = "https://example.test/slow"
    client = _client(retries=0)
    responses.add(responses.GET, url, body=requests.Timeout("timed out"))

    with pytest.raises(requests.Timeout, match="timed out"):
        client.get(url)


@responses.activate
def test_robots_txt_denial_prevents_page_request() -> None:
    url = "https://example.test/private/data"
    client = _client(respect_robots_txt=True)
    responses.add(
        responses.GET,
        "https://example.test/robots.txt",
        status=200,
        body="User-agent: *\nDisallow: /private/\n",
    )

    with pytest.raises(RobotsDeniedError):
        client.get(url)

    assert [call.request.url for call in responses.calls] == [
        "https://example.test/robots.txt"
    ]


def test_domain_rate_limiter_only_waits_for_repeated_origin() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def sleeper(delay: float) -> None:
        sleeps.append(delay)
        now[0] += delay

    limiter = DomainRateLimiter(1.0, clock=clock, sleeper=sleeper)
    limiter.wait("https://example.test/one")
    limiter.wait("https://example.test/two")
    limiter.wait("https://another.test/one")

    assert sleeps == [1.0]


def test_conditional_store_ignores_invalid_cache_and_persists_atomically(
    tmp_path,
) -> None:
    cache_path = tmp_path / "nested" / "conditional.json"
    cache_path.parent.mkdir()
    cache_path.write_text("not-json", encoding="utf-8")

    store = ConditionalRequestStore(cache_path)
    assert store.get_headers("https://example.test/") == {}
    store.update("https://example.test/", {"etag": '"new"'})

    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["https://example.test/"]["etag"] == '"new"'
    assert not cache_path.with_suffix(".json.tmp").exists()
