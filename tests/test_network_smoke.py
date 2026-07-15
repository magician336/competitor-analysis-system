from __future__ import annotations

import os

import pytest

from crawler.http_client import HttpClient, HttpClientConfig


pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("CODERADAR_RUN_NETWORK_TESTS") != "1",
        reason="set CODERADAR_RUN_NETWORK_TESTS=1 to enable public-source smoke tests",
    ),
]


def test_cursor_official_page_is_reachable() -> None:
    """Opt-in only: verify one configured primary source through shared HTTP policy."""

    with HttpClient(
        HttpClientConfig(
            timeout_seconds=15,
            retries=1,
            requests_per_second=1,
            trust_environment=True,
            respect_robots_txt=True,
        )
    ) as client:
        response = client.get(
            "https://cursor.com/",
            conditional=False,
            force=True,
        )

    assert 200 <= response.status_code < 400
    assert response.content
