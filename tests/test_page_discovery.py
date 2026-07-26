from __future__ import annotations

import json

import pytest

from crawler.page_discovery import discover_page_urls


def test_generic_html_links_are_canonicalized_and_kept_same_origin() -> None:
    html = """
    <a href="/docs/start?utm_source=newsletter&tab=agent#usage">Start</a>
    <a href="https://DOCS.EXAMPLE.test:443/docs/start?tab=agent">Duplicate</a>
    <a href="//docs.example.test/docs/next?fbclid=secret">Next</a>
    <a href="https://other.example.test/docs/outside">Cross origin</a>
    <a href="http://docs.example.test/docs/downgrade">Other scheme</a>
    <a href="javascript:alert(1)">Script</a>
    <a href="https://user@docs.example.test/docs/credential">Userinfo</a>
    <a href="/docs/%2e%2e/admin">Encoded traversal</a>
    <a href="../outside">Relative traversal</a>
    """

    urls = discover_page_urls(
        html,
        page_url="https://docs.example.test/docs/index?_lang=zh",
        allowed_path_prefixes=("/docs",),
        query_params={"_lang": "en"},
    )

    assert urls == [
        "https://docs.example.test/docs/start?_lang=en&tab=agent",
        "https://docs.example.test/docs/next?_lang=en",
    ]


def test_prefix_regex_deduplication_and_maximum_are_strict() -> None:
    html = """
    <a href="/docs/a">A</a>
    <a href="/docs2/not-a-prefix-match">Boundary</a>
    <a href="/docs/private/hidden">Private</a>
    <a href="/docs/draft-note">Draft</a>
    <a href="/docs/b">B</a>
    <a href="/docs/c">C</a>
    """

    urls = discover_page_urls(
        html,
        page_url="https://example.test/",
        allowed_path_prefixes=("docs",),
        excluded_path_prefixes=("/docs/private",),
        include_patterns=(r"^/docs/",),
        exclude_patterns=(r"draft",),
        max_urls=2,
    )

    assert urls == [
        "https://example.test/docs/a",
        "https://example.test/docs/b",
    ]


def test_trae_router_document_extracts_layout_and_dollar_structures() -> None:
    layout = {
        "pageDetail": {"path": "ide"},
        "busStructure": [
            {"path": "what-is-trae", "is_dir": False},
            {"path": "guides", "is_dir": True},
            {
                "path": "nested",
                "is_dir": True,
                "subs": [
                    {"path": "agent-mode", "is_dir": False},
                    {"path": "../unsafe", "is_dir": False},
                ],
            },
        ],
    }
    dollar = {
        "pageDetail": {"path": "solo"},
        "busStructure": [
            {"path": "solo/getting-started", "is_dir": 0},
            {"path": "duplicate", "is_dir": 1},
        ],
    }
    payload = {"loaderData": {"layout": layout, "$": dollar}}
    html = (
        '<a href="/ide/from-anchor?_lang=zh&utm_campaign=launch">Anchor</a>'
        f"<script>window._ROUTER_DATA = {json.dumps(payload)};</script>"
    )

    urls = discover_page_urls(
        html,
        page_url="https://docs.trae.ai/ide/what-is-trae?_lang=en",
        embedded_mode="trae_router_document",
        allowed_path_prefixes=("/ide", "/solo"),
        query_params={"_lang": "en"},
        max_urls=20,
    )

    assert urls == [
        "https://docs.trae.ai/ide/from-anchor?_lang=en",
        "https://docs.trae.ai/ide/what-is-trae?_lang=en",
        "https://docs.trae.ai/ide/agent-mode?_lang=en",
        "https://docs.trae.ai/solo/getting-started?_lang=en",
    ]


def test_trae_router_requires_supported_page_root_and_non_directory_path() -> None:
    payload = {
        "loaderData": {
            "layout": {
                "pageDetail": {"path": "blog"},
                "busStructure": [{"path": "post", "is_dir": False}],
            },
            "$": {
                "pageDetail": {"path": "plugin"},
                "busStructure": [
                    {"path": "", "is_dir": False},
                    {"path": "install", "is_dir": "false"},
                    {"path": "catalog", "is_dir": "true"},
                ],
            },
        }
    }
    html = f"<script>window['_ROUTER_DATA']={json.dumps(payload)}</script>"

    assert discover_page_urls(
        html,
        page_url="https://docs.trae.ai/plugin",
        embedded_mode="trae_router_document",
    ) == ["https://docs.trae.ai/plugin/install"]


def test_malformed_router_data_is_ignored_without_hiding_anchor_links() -> None:
    html = """
    <a href="/ide/valid">Valid</a>
    <script>window._ROUTER_DATA = {invalid JSON};</script>
    """

    assert discover_page_urls(
        html.encode(),
        page_url="https://docs.trae.ai/ide",
        embedded_mode="trae_router_document",
    ) == ["https://docs.trae.ai/ide/valid"]


@pytest.mark.parametrize("max_urls", [-1, True, 1.5])
def test_invalid_maximum_is_rejected(max_urls: object) -> None:
    with pytest.raises(ValueError, match="max_urls"):
        discover_page_urls(
            '<a href="/one">One</a>',
            page_url="https://example.test/",
            max_urls=max_urls,  # type: ignore[arg-type]
        )


def test_unknown_embedded_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported embedded_mode"):
        discover_page_urls(
            "",
            page_url="https://example.test/",
            embedded_mode="unknown",
        )
