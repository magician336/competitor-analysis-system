from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from processing.cleaners import clean_html, clean_json, clean_rss, extract_items
from schemas.document import RawRecord, SourceType


def _raw_record(source_type: str, *, content_type: str, path: str) -> RawRecord:
    return RawRecord(
        crawl_run_id="run-001",
        competitor="Cursor",
        source_type=source_type,
        requested_url="https://example.test/source",
        canonical_url="https://example.test/source",
        fetched_at=datetime(2026, 7, 13, 8, tzinfo=timezone.utc),
        http_status=200,
        content_type=content_type,
        payload_path=path,
    )


def test_clean_html_removes_non_content_and_preserves_structure(fixture_text) -> None:
    cleaned = clean_html(fixture_text("pages/official.html"))

    assert "# Cursor Agent" in cleaned
    assert "## Code intelligence" in cleaned
    assert "### Workflow" in cleaned
    assert "- Repository-wide context" in cleaned
    assert "- IDE integration" in cleaned
    assert "1. Index project" in cleaned
    assert "2. Review change" in cleaned
    assert "  - Run tests" in cleaned
    assert "Supports Python and TypeScript." in cleaned
    assert cleaned.count("Supports Python and TypeScript.") == 1
    assert "window.tracker" not in cleaned
    assert "Products Pricing Sign in" not in cleaned
    assert "Accept all cookies" not in cleaned
    assert "Cookie preference center" not in cleaned
    assert "Copyright Example" not in cleaned


def test_clean_html_preserves_pricing_table_values(fixture_text) -> None:
    cleaned = clean_html(fixture_text("pages/pricing.html"))

    for expected in ("Plans and pricing", "Hobby", "$0", "Pro", "$20", "per month"):
        assert expected in cleaned
    assert "| Plan | Price | Billing |" in cleaned
    assert "| --- | --- | --- |" in cleaned
    assert "| Hobby | $0 | monthly |" in cleaned
    assert "| Pro | $20 | per month |" in cleaned
    assert "Privacy Terms" not in cleaned


def test_clean_html_removes_nested_doctype_and_hidden_text() -> None:
    cleaned = clean_html(
        """
        <main>
          <!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
          <h1>Visible heading</h1>
          <span hidden>Hidden helper</span>
          <p>Visible body.</p>
        </main>
        """
    )

    assert cleaned == "# Visible heading\n\nVisible body."


def test_clean_html_keeps_one_complete_responsive_faq_representation() -> None:
    cleaned = clean_html(
        """
        <main><section id="faq">
          <h2>Frequently asked questions</h2>
          <div class="FAQGroup-module__FAQGroup__accordion">
            <h3>General</h3>
            <h4>What is Copilot?</h4>
            <p>Copilot is an AI coding assistant with repository context.</p>
            <p>This complete accordion answer is deliberately long enough to
            represent the substantive mobile FAQ content for this fixture.</p>
          </div>
          <div>
            <div role="tablist"><button role="tab">General</button></div>
            <div role="tabpanel">
              <h3>General</h3>
              <h4>What is Copilot?</h4>
              <p>Copilot is an AI coding assistant with repository context.</p>
            </div>
          </div>
        </section></main>
        """
    )

    assert cleaned.count("What is Copilot?") == 1
    assert cleaned.count("repository context") == 1


def test_extract_html_honors_declared_non_utf8_charset() -> None:
    record = _raw_record(
        "official",
        content_type="text/html; charset=gb18030",
        path="lingma/official/run/page.html",
    )
    payload = (
        "<html><head><title>通义灵码</title></head>"
        "<body><main><h1>通义灵码</h1><p>智能编码助手</p></main></body></html>"
    ).encode("gb18030")

    [item] = extract_items(record, payload)

    assert item.title == "通义灵码"
    assert "智能编码助手" in item.content
    assert "\ufffd" not in item.content


def test_extract_cursor_next_rsc_document_from_embedded_payload() -> None:
    record = _raw_record(
        "product_docs",
        content_type="text/html; charset=utf-8",
        path="cursor/product_docs/run/agent.html",
    )
    record.source_metadata = {"embedded_content": "cursor_next_rsc"}
    rsc = (
        '1:["$","main",null,{"children":"Cursor Agent"}]\n'
        '2:["$","p",null,{"children":"Agent searches the codebase, edits files, '
        'and runs terminal commands."}]'
    )
    html = (
        "<html><head><meta property='og:title' content='Overview | Cursor Docs'>"
        "</head><body><main></main>"
        f"<script>self.__next_f.push([1,{json.dumps(rsc)}])</script>"
        "</body></html>"
    )

    [item] = extract_items(record, html)

    assert item.title == "Overview | Cursor Docs"
    assert "Cursor Agent" in item.content
    assert "runs terminal commands" in item.content
    assert item.source_metadata["embedded_content_extraction"] == "cursor_next_rsc"


def test_extract_trae_router_document_from_embedded_payload() -> None:
    record = _raw_record(
        "product_docs",
        content_type="text/html; charset=utf-8",
        path="trae/product_docs/run/overview.html",
    )
    record.source_metadata = {"embedded_content": "trae_router_document"}
    record.final_url = "https://example.test/ide/what-is-trae"
    router_data = {
        "loaderData": {
            "layout": {
                "docDetail": {
                    "_id": "doc-1",
                    "version_id": "version-2",
                    "title": "What is TRAE IDE?",
                    "publish_at": "2026-07-01T08:30:00Z",
                    "content": {
                        "children": [
                            {
                                "props": {
                                    "value": {
                                        "ops": [
                                            {"insert": "TRAE is an AI coding editor.\\n"},
                                            {"insert": "It supports agent workflows.\\n"},
                                        ]
                                    }
                                }
                            }
                        ]
                    },
                }
            }
        }
    }
    html = (
        "<html><body><main></main><script>"
        f"window._ROUTER_DATA = {json.dumps(router_data)}"
        "</script></body></html>"
    )

    [item] = extract_items(record, html)

    assert item.title == "What is TRAE IDE?"
    assert item.content.startswith("# What is TRAE IDE?")
    assert "agent workflows" in item.content
    assert item.publish_time == datetime(2026, 7, 1, 8, 30, tzinfo=timezone.utc)
    assert item.url == "https://example.test/ide/what-is-trae"
    assert item.source_metadata["publisher_document_id"] == "doc-1"


def test_extract_configured_json_document_uses_public_canonical_url() -> None:
    record = _raw_record(
        "security_privacy",
        content_type="application/json",
        path="trae/security_privacy/run/privacy.json",
    )
    record.source_metadata = {
        "json_document": {
            "result_path": "Result",
            "title_field": "Title",
            "content_fields": ["Content"],
            "publish_time_field": "SubTitle",
        }
    }
    payload = {
        "Result": {
            "Title": "TRAE Privacy Policy",
            "SubTitle": "June 30, 2026",
            "Content": "# Introduction\n\nThis policy explains data processing.",
        }
    }

    [item] = extract_items(record, payload)

    assert item.title == "TRAE Privacy Policy"
    assert "data processing" in item.content
    assert item.url == "https://example.test/source"
    assert item.publish_time == datetime(2026, 6, 30, tzinfo=timezone.utc)
    assert item.source_metadata["structured_json_extraction"] is True


def test_extract_configured_json_items_supports_paths_and_url_templates() -> None:
    record = _raw_record(
        "community",
        content_type="application/json",
        path="cursor/community/run/latest.json",
    )
    record.source_metadata = {
        "json_items": {
            "result_path": "topic_list.topics",
            "title_field": "fancy_title",
            "content_fields": ["excerpt", "tags"],
            "url_template": "/t/{slug}/{id}",
            "url_base": "https://forum.cursor.com",
            "publish_time_field": "created_at",
            "author_field": "posters.0.user_id",
            "identity_template": "topic:{id}",
            "identity_mode": "configured",
        }
    }
    payload = {
        "topic_list": {
            "topics": [
                {
                    "id": 42,
                    "slug": "agent-debugging",
                    "fancy_title": "Agent debugging",
                    "excerpt": "<p>The agent stops after a tool call.</p>",
                    "tags": ["agent", "debugging"],
                    "created_at": "2026-07-20T08:30:00Z",
                    "posters": [{"user_id": 7}],
                }
            ]
        }
    }

    [item] = extract_items(record, payload)

    assert item.url == "https://forum.cursor.com/t/agent-debugging/42"
    assert item.author == "7"
    assert item.publish_time == datetime(2026, 7, 20, 8, 30, tzinfo=timezone.utc)
    assert (
        item.source_metadata["identity_key"]
        == "discourse-topic:https://forum.cursor.com:42"
    )
    assert item.source_metadata["configured_identity"] == "topic:42"


def test_configured_json_url_template_escapes_untrusted_fields() -> None:
    record = _raw_record(
        "community",
        content_type="application/json",
        path="cursor/community/run/latest.json",
    )
    record.source_metadata = {
        "json_items": {
            "result_path": "topics",
            "title_field": "title",
            "content_fields": ["excerpt"],
            "url_template": "/t/{slug}/{id}",
            "url_base": "https://forum.cursor.com",
        }
    }
    payload = {
        "topics": [
            {
                "id": 42,
                "slug": "../../admin?next=https://evil.test/#fragment",
                "title": "Escaped topic",
                "excerpt": "Safe evidence text.",
            }
        ]
    }

    [item] = extract_items(record, payload)

    assert item.url == (
        "https://forum.cursor.com/t/"
        "..%2F..%2Fadmin%3Fnext%3Dhttps%3A%2F%2Fevil.test%2F%23fragment/42"
    )
    assert item.source_metadata["identity_key"] == f"canonical-url:{item.url}"


def test_configured_json_url_field_rejects_external_origin() -> None:
    record = _raw_record(
        "community",
        content_type="application/json",
        path="cursor/community/run/latest.json",
    )
    record.source_metadata = {
        "json_items": {
            "result_path": "topics",
            "title_field": "title",
            "content_fields": ["excerpt"],
            "url_field": "url",
            "url_base": "https://forum.cursor.com",
        }
    }
    payload = {
        "topics": [
            {
                "title": "External topic",
                "excerpt": "Untrusted redirect target.",
                "url": "https://evil.test/topic",
            }
        ]
    }

    assert extract_items(record, payload) == []


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://forum.cursor.com:bad/topic",
        "https://forum.cursor.com:99999/topic",
        "https://[::1/topic",
    ],
)
def test_configured_json_url_field_rejects_malformed_url(
    unsafe_url: str,
) -> None:
    record = _raw_record(
        "community",
        content_type="application/json",
        path="cursor/community/run/latest.json",
    )
    record.source_metadata = {
        "json_items": {
            "result_path": "topics",
            "title_field": "title",
            "content_fields": ["excerpt"],
            "url_field": "url",
            "url_base": "https://forum.cursor.com",
        }
    }

    assert extract_items(
        record,
        {
            "topics": [
                {
                    "title": "Malformed topic",
                    "excerpt": "Evidence text.",
                    "url": unsafe_url,
                }
            ]
        },
    ) == []


def test_configured_json_url_preserves_directory_base() -> None:
    record = _raw_record(
        "changelog",
        content_type="application/json",
        path="codegeex/changelog/run/releases.json",
    )
    record.source_metadata = {
        "json_items": {
            "result_path": "items",
            "title_field": "title",
            "content_fields": ["notes"],
            "url_field": "link",
            "url_base": "https://example.test/api/plugins/",
            "identity_field": "id",
        }
    }

    [item] = extract_items(
        record,
        {
            "items": [
                {
                    "id": 1,
                    "title": "1.0",
                    "notes": "Release evidence.",
                    "link": "versions/1",
                }
            ]
        },
    )

    assert item.url == "https://example.test/api/plugins/versions/1"


def test_configured_json_url_template_rejects_exact_dot_segment() -> None:
    record = _raw_record(
        "community",
        content_type="application/json",
        path="cursor/community/run/latest.json",
    )
    record.source_metadata = {
        "json_items": {
            "result_path": "topics",
            "title_field": "title",
            "content_fields": ["excerpt"],
            "url_template": "/t/{slug}/{id}",
            "url_base": "https://forum.cursor.com",
        }
    }

    assert extract_items(
        record,
        {
            "topics": [
                {
                    "id": 42,
                    "slug": "..",
                    "title": "Dot segment",
                    "excerpt": "Evidence text.",
                }
            ]
        },
    ) == []


def test_configured_identity_is_stable_across_url_changes_and_separates_ids() -> None:
    record = _raw_record(
        "community",
        content_type="application/json",
        path="cursor/community/run/latest.json",
    )
    record.source_metadata = {
        "json_items": {
            "result_path": "topics",
            "title_field": "title",
            "content_fields": ["excerpt"],
            "url_template": "/t/{slug}",
            "url_base": "https://forum.cursor.com",
            "identity_field": "id",
        }
    }

    first, second, third = extract_items(
        record,
        {
            "topics": [
                {"id": 1, "slug": "old", "title": "First", "excerpt": "One"},
                {"id": 1, "slug": "new", "title": "First", "excerpt": "Two"},
                {"id": 2, "slug": "new", "title": "Second", "excerpt": "Three"},
            ]
        },
    )

    assert first.source_metadata["identity_key"] == "configured-json:1"
    assert second.source_metadata["identity_key"] == "configured-json:1"
    assert third.source_metadata["identity_key"] == "configured-json:2"
    assert first.url != second.url
    assert second.url == third.url


def test_clean_html_falls_back_when_empty_main_precedes_streamed_content() -> None:
    cleaned = clean_html(
        """
        <html><body><main></main></body>
        <div id="streamed-content">
          <h1>通义灵码</h1>
          <p>面向研发任务的智能编码助手，支持代码补全、单元测试生成、代码优化、
          研发智能问答、工程理解和多文件修改，帮助开发者完成需求分析、编码、测试与维护。</p>
        </div></html>
        """
    )

    assert "# 通义灵码" in cleaned
    assert "智能编码助手" in cleaned


def test_changelog_html_prefers_primary_article_and_extracts_bare_version() -> None:
    record = _raw_record(
        "changelog",
        content_type="text/html",
        path="cursor/changelog/run/page.html",
    )
    html = """
    <main>
      <article>
        <span class="label">3.11</span>
        <h1>Side Chats</h1>
        <p>The agent can search conversations.</p>
      </article>
      <section><h2>Related security news</h2><p>Privacy and billing.</p></section>
    </main>
    """

    [item] = extract_items(record, html)

    assert item.content == "3.11\n\n# Side Chats\n\nThe agent can search conversations."
    assert "Related security news" not in item.content
    assert item.raw_version == "3.11"
    assert item.product_version == "3.11.0"


def test_changelog_html_removes_cursor_date_and_section_page_lead() -> None:
    record = _raw_record(
        "changelog",
        content_type="text/html",
        path="cursor/changelog/run/customize.html",
    )
    html = """
    <main>
      <p>3.9 Jun 22, 2026 · Changelog</p>
      <p>Changelog</p>
      <h1>Customize Cursor</h1>
      <p>Plugins, skills, and MCPs can be managed in one place.</p>
    </main>
    """

    [item] = extract_items(record, html)

    assert item.content == (
        "# Customize Cursor\n\n"
        "Plugins, skills, and MCPs can be managed in one place."
    )


def test_aliyun_help_page_uses_document_body_without_header_controls() -> None:
    record = _raw_record(
        "pricing",
        content_type="text/html",
        path="tongyi_lingma/pricing/run/billing.html",
    )
    record = record.model_copy(
        update={
            "canonical_url": "https://help.aliyun.com/zh/lingma/billing",
            "final_url": "https://help.aliyun.com/zh/lingma/billing",
        }
    )
    html = """
    <main id="aliyun-docs-view">
      <section class="aliyun-docs-content">
        <header><a>首页</a><h1>计费说明</h1><span>复制 MD 格式</span></header>
        <div class="markdown-body"><main id="main-1">
          <p>本文介绍产品价格。</p>
          <h2>付费方式</h2><p>订阅按席位和月份计费。</p>
        </main></div>
      </section>
    </main>
    """

    [item] = extract_items(record, html)

    assert item.content == (
        "# 计费说明\n\n本文介绍产品价格。\n\n"
        "## 付费方式\n\n订阅按席位和月份计费。"
    )
    assert "首页" not in item.content
    assert "复制 MD 格式" not in item.content


def test_aliyun_changelog_prefers_modified_time_and_directory_body(
    fixture_text,
) -> None:
    record = _raw_record(
        "changelog",
        content_type="text/html; charset=utf-8",
        path="tongyi_lingma/changelog/run/qoder.html",
    ).model_copy(
        update={
            "canonical_url": "https://help.aliyun.com/zh/lingma/qoder-cn-update-log",
            "final_url": "https://help.aliyun.com/zh/lingma/qoder-cn-update-log",
        }
    )

    [item] = extract_items(
        record,
        fixture_text("pages/aliyun_changelog_detail_recent.html"),
    )

    assert item.title == "Qoder CN 更新日志"
    assert item.publish_time == datetime(2026, 7, 10, 8, tzinfo=timezone.utc)
    assert "项目级上下文检索" in item.content
    assert "首页" not in item.content
    assert "帮助中心页脚" not in item.content



def test_clean_json_is_deterministic_and_omits_transport_only_fields(
    fixture_json,
) -> None:
    issue = fixture_json("github/issues_page1.json")[0]

    first = clean_json(issue)
    second = clean_json(json.dumps(issue))

    assert first == second
    assert "Agent stalls in large repository" in first
    assert "The agent becomes slow" in first
    assert "https://api.github.com" not in first


def test_clean_rss_extracts_entry_title_and_readable_body(fixture_text) -> None:
    cleaned = clean_rss(fixture_text("feeds/changelog.xml"))

    assert "Copilot coding agent update" in cleaned
    assert "The coding agent can now edit multiple files." in cleaned
    assert "<p>" not in cleaned
    assert clean_rss("<rss><broken>") == ""


def test_extract_html_metadata_includes_title_time_content_and_version(
    fixture_text,
) -> None:
    record = _raw_record(
        "changelog",
        content_type="text/html; charset=utf-8",
        path="cursor/changelog/run/page.html",
    )

    [item] = extract_items(record, fixture_text("pages/changelog.html"))

    assert item.title == "Agent 2.0"
    assert item.url == "https://example.test/source"
    assert item.publish_time == datetime(2026, 7, 1, 8, 30, tzinfo=timezone.utc)
    assert "Improved completion latency" in item.content
    assert item.raw_version is None
    assert item.product_version is None


def test_extract_rss_items_preserves_entry_url_and_publish_time(fixture_text) -> None:
    record = _raw_record(
        "changelog",
        content_type="application/rss+xml",
        path="copilot/changelog/run/feed.xml",
    )

    [item] = extract_items(record, fixture_text("feeds/changelog.xml"))

    assert item.title == "Copilot coding agent update"
    assert item.url == "https://example.test/changelog/copilot-agent"
    assert item.publish_time == datetime(2026, 7, 1, 8, 30, tzinfo=timezone.utc)
    assert item.content == "The coding agent can now edit multiple files."


def test_extract_rss_source_uses_configured_format_when_server_mislabels_content(
    fixture_text,
) -> None:
    record = _raw_record(
        "rss",
        content_type="text/plain",
        path="trae/rss/run/feed.txt",
    )
    record.source_metadata["format"] = "rss"

    [item] = extract_items(record, fixture_text("feeds/changelog.xml"))

    assert item.title == "Copilot coding agent update"
    assert item.url == "https://example.test/changelog/copilot-agent"
    assert item.publish_time == datetime(2026, 7, 1, 8, 30, tzinfo=timezone.utc)
    assert (
        item.source_metadata["identity_key"]
        == "canonical-url:https://example.test/changelog/copilot-agent"
    )


def test_extract_atom_prefers_link_href_over_opaque_entry_id() -> None:
    record = _raw_record(
        "community",
        content_type="application/atom+xml",
        path="trae/community/run/discussions.atom",
    )
    record.requested_url = "https://github.com/org/repo/discussions.atom"
    record.canonical_url = "https://github.com/org/repo/discussions.atom"
    payload = (
        "<feed xmlns='http://www.w3.org/2005/Atom'><entry>"
        "<id>tag:github.com,2008:Discussion/123</id>"
        "<title>Repository feedback</title>"
        "<link rel='alternate' href='https://github.com/org/repo/discussions/123'/>"
        "<updated>2026-07-01T08:30:00Z</updated>"
        "<content>Agent feedback from a public discussion.</content>"
        "</entry></feed>"
    )

    [item] = extract_items(record, payload)

    assert item.url == "https://github.com/org/repo/discussions/123"
    assert item.source_metadata["feed_entry_id"] == "tag:github.com,2008:Discussion/123"
    assert (
        item.source_metadata["identity_key"]
        == "canonical-url:https://github.com/org/repo/discussions/123"
    )


def test_extract_atom_prefers_html_alternate_link() -> None:
    record = _raw_record(
        "community",
        content_type="application/atom+xml",
        path="cursor/community/run/latest.atom",
    )
    payload = (
        "<feed xmlns='http://www.w3.org/2005/Atom'><entry>"
        "<id>topic-1</id><title>Topic</title>"
        "<link rel='self' type='application/atom+xml' "
        "href='https://example.test/api/topic-1.xml'/>"
        "<link rel='alternate' type='text/html' "
        "href='https://example.test/t/topic/1'/>"
        "<content>Evidence text.</content>"
        "</entry></feed>"
    )

    [item] = extract_items(record, payload)

    assert item.url == "https://example.test/t/topic/1"


@pytest.mark.parametrize(
    "unsafe_link",
    [
        "javascript:alert(1)",
        "data:text/html,boom",
        "//evil.test/topic",
        "https://evil.test/topic",
    ],
)
def test_feed_item_rejects_unsafe_or_unlisted_origin(unsafe_link: str) -> None:
    record = _raw_record(
        "community",
        content_type="application/atom+xml",
        path="cursor/community/run/latest.atom",
    )
    payload = (
        "<feed xmlns='http://www.w3.org/2005/Atom'><entry>"
        "<id>topic-1</id><title>Topic</title>"
        f"<link rel='alternate' href='{unsafe_link}'/>"
        "<content>Evidence text.</content>"
        "</entry></feed>"
    )

    [item] = extract_items(record, payload)

    assert item.url == "https://example.test/source"
    assert item.source_metadata["identity_key"] == "feed-entry:topic-1"


def test_feed_item_accepts_explicit_allowed_origin() -> None:
    record = _raw_record(
        "rss",
        content_type="application/rss+xml",
        path="trae/rss/run/feed.xml",
    )
    record.source_metadata["allowed_item_origins"] = ["https://trae.ai"]
    payload = (
        "<rss><channel><item><guid>post-1</guid><title>TRAE update</title>"
        "<link>https://trae.ai/blog/post-1</link>"
        "<description>Product evidence.</description>"
        "</item></channel></rss>"
    )

    [item] = extract_items(record, payload)

    assert item.url == "https://trae.ai/blog/post-1"
    assert item.source_metadata["identity_key"] == (
        "canonical-url:https://trae.ai/blog/post-1"
    )


def test_extract_github_release_envelope_normalizes_version(fixture_json) -> None:
    release = fixture_json("github/releases_page1.json")[0]
    record = _raw_record(
        "github_release",
        content_type="application/json",
        path="copilot/github_release/run/release.json",
    )
    envelope = {
        "kind": "github_release",
        "repository": "example/copilot",
        "item": release,
    }

    [item] = extract_items(record, envelope)

    assert item.title == "Agent 1.2"
    assert item.raw_version == "v1.2.0"
    assert item.product_version == "1.2.0"
    assert item.author == "release-bot"
    assert item.source_metadata["repository"] == "example/copilot"


def test_extract_github_issue_includes_comments_and_risk_metadata(fixture_json) -> None:
    issue = fixture_json("github/issues_page1.json")[0]
    comments = fixture_json("github/issue_42_comments.json")
    record = _raw_record(
        "github_issue",
        content_type="application/json",
        path="copilot/github_issue/run/issue.json",
    )
    envelope = {
        "kind": "github_issue",
        "repository": "example/copilot",
        "item": issue,
        "comments": comments,
    }

    [item] = extract_items(record, envelope)

    assert item.title == "Agent stalls in large repository"
    assert "Comment by maintainer" in item.content
    assert "generated files" in item.content
    assert item.author == "alice"
    assert item.source_metadata == {
        "github_kind": "github_issue",
        "repository": "example/copilot",
        "issue_number": 42,
        "issue_state": "open",
        "labels": ["bug"],
        "comment_count": 1,
        "declared_comment_count": 1,
        "comments_truncated": False,
    }


def test_github_issue_cleans_embedded_html_and_marks_truncated_comments() -> None:
    record = _raw_record(
        "github_issue",
        content_type="application/json",
        path="codegeex/github_issue/run/issue.json",
    )
    envelope = {
        "kind": "github_issue",
        "repository": "example/repo",
        "item": {
            "number": 7,
            "title": "Rendering failure",
            "body": (
                "## Failure\n<!-- internal note --><script>unsafe()</script>"
                "<details><summary>Logs</summary><p>failed<br>again</p></details>"
                "<img src='https://example.test/shot.png' alt='error screenshot'>"
            ),
            "state": "open",
            "comments": 3,
            "html_url": "https://github.com/example/repo/issues/7",
            "created_at": "2026-07-01T00:00:00Z",
            "labels": [],
            "user": {"login": "reporter"},
        },
        "comments": [
            {
                "body": "<p>First collected comment.</p>",
                "user": {"login": "maintainer"},
            }
        ],
    }

    [item] = extract_items(record, envelope)

    assert "unsafe()" not in item.content
    assert "internal note" not in item.content
    assert "<img" not in item.content
    assert "Logs" in item.content
    assert "failed\nagain" in item.content
    assert "[Image: error screenshot]" in item.content
    assert item.source_metadata["comment_count"] == 1
    assert item.source_metadata["declared_comment_count"] == 3
    assert item.source_metadata["comments_truncated"] is True


def test_configured_changelog_identity_is_stable_across_date_heading_format() -> None:
    record = _raw_record(
        "changelog",
        content_type="text/html",
        path="tongyi_lingma/changelog/run/detail.html",
    )
    record.source_metadata = {
        "discovery_mode": "configured_undated_directory",
        "undated_detail_root_selector": ".markdown-body",
        "cutoff": "2026-01-01T00:00:00Z",
    }
    first = """
    <html><head><title>Qoder CN 更新日志</title></head><body>
      <div class="markdown-body"><h2>2026年7月10日</h2><p>新增任务规划。</p></div>
    </body></html>
    """
    second = """
    <html><head><title>Qoder CN 更新日志</title></head><body>
      <div class="markdown-body"><h2>2026-07-10</h2><p>新增任务规划与检查。</p></div>
    </body></html>
    """

    [first_item] = extract_items(record, first)
    [second_item] = extract_items(record, second)

    assert first_item.source_metadata["identity_key"] == second_item.source_metadata[
        "identity_key"
    ]
