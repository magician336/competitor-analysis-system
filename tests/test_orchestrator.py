from __future__ import annotations

from pathlib import Path

import requests
import responses

from crawler.http_client import HttpClient, HttpClientConfig
from crawler.models import CollectorTask, SourceType
from crawler.orchestrator import CrawlOrchestrator


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _offline_client() -> HttpClient:
    return HttpClient(
        HttpClientConfig(
            retries=0,
            requests_per_second=0,
            trust_environment=False,
            respect_robots_txt=False,
        )
    )


def test_collector_task_parses_aliases_and_common_yaml_shapes() -> None:
    task = CollectorTask.from_mapping(
        "cursor",
        "website",
        {
            "enabled": True,
            "url": "https://cursor.com/",
            "evidence_level": "A",
        },
        max_issues=10,
    )
    github = CollectorTask.from_mapping(
        "github_copilot",
        "github",
        {"repositories": ["microsoft/vscode-copilot-chat"]},
    )

    assert task.source_type is SourceType.OFFICIAL
    assert task.urls == ("https://cursor.com/",)
    assert task.metadata == {"evidence_level": "A"}
    assert github.repositories == ("microsoft/vscode-copilot-chat",)

    assert SourceType.parse("docs") is SourceType.PRODUCT_DOCS
    assert SourceType.parse("status") is SourceType.STATUS_PAGE
    assert SourceType.parse("marketplace") is SourceType.PLUGIN_MARKETPLACE
    assert SourceType.parse("forum") is SourceType.COMMUNITY
    assert SourceType.parse("privacy") is SourceType.SECURITY_PRIVACY
    assert SourceType.parse("benchmarks") is SourceType.BENCHMARK


def test_real_configuration_declares_all_source_categories() -> None:
    import yaml

    config = yaml.safe_load(
        (PROJECT_ROOT / "config" / "competitors.yaml").read_text(encoding="utf-8")
    )
    expected = {
        "official",
        "changelog",
        "pricing",
        "product_docs",
        "status_page",
        "github",
        "plugin_marketplace",
        "community",
        "review",
        "security_privacy",
        "benchmark",
    }

    for competitor in config["competitors"]:
        assert set(competitor["sources"]) == expected
        for source in competitor["sources"].values():
            assert source["evidence_level"] in {"A", "B", "C", "D"}
            assert isinstance(source["enabled"], bool)


def test_real_configuration_builds_only_enabled_selected_tasks(tmp_path) -> None:
    orchestrator = CrawlOrchestrator.from_yaml(
        PROJECT_ROOT / "config" / "competitors.yaml",
        tmp_path / "data",
        client=_offline_client(),
    )

    tasks, errors = orchestrator.build_tasks(
        competitors="cursor,github_copilot",
        sources="official,pricing,github",
        since_days=90,
        max_issues=12,
        max_comments=3,
        force=True,
    )

    assert errors == []
    assert {(task.competitor, task.source_type.value) for task in tasks} == {
        ("cursor", "official"),
        ("cursor", "pricing"),
        ("github_copilot", "official"),
        ("github_copilot", "pricing"),
        ("github_copilot", "github"),
    }
    github_task = next(task for task in tasks if task.source_type is SourceType.GITHUB)
    assert github_task.repositories == ("microsoft/vscode-copilot-chat",)
    assert github_task.max_issues == 12
    assert github_task.max_comments == 3
    assert github_task.force is True


def test_broad_github_selection_preserves_configured_subtype_switches(
    tmp_path,
) -> None:
    config = {
        "competitors": [
            {
                "id": "trae",
                "sources": {
                    "github": {
                        "enabled": True,
                        "repositories": ["Trae-AI/TRAE"],
                        "collect_releases": False,
                        "collect_issues": True,
                    }
                },
            }
        ]
    }
    orchestrator = CrawlOrchestrator(
        config,
        tmp_path / "data",
        client=_offline_client(),
    )

    [task], errors = orchestrator.build_tasks(sources="github")

    assert errors == []
    assert task.metadata["collect_releases"] is False
    assert task.metadata["collect_issues"] is True


def test_tongyi_changelog_task_keeps_bounded_undated_directory_rules(tmp_path) -> None:
    orchestrator = CrawlOrchestrator.from_yaml(
        PROJECT_ROOT / "config" / "competitors.yaml",
        tmp_path / "data",
        client=_offline_client(),
    )

    tasks, errors = orchestrator.build_tasks(
        competitors="tongyi_lingma",
        sources="changelog",
        since_days=365,
    )

    assert errors == []
    [task] = tasks
    assert task.metadata["undated_entry_selector"] == (
        ".aliyun-docs-content .markdown-body .directory a[href]"
    )
    assert task.metadata["undated_entry_path_prefixes"] == ["/zh/lingma/"]
    assert task.metadata["undated_entry_text_pattern"] == (
        "更新日志|日志（存档）|产品公告"
    )
    assert task.metadata["undated_detail_root_selector"] == (
        ".aliyun-docs-content .markdown-body"
    )


def test_default_conditional_cache_uses_snapshots_directory(tmp_path) -> None:
    orchestrator = CrawlOrchestrator.from_yaml(
        PROJECT_ROOT / "config" / "competitors.yaml",
        tmp_path / "data",
    )
    try:
        assert orchestrator.raw_root == (tmp_path / "data" / "raw").resolve()
        assert orchestrator.snapshot_root == (
            tmp_path / "data" / "snapshots"
        ).resolve()
        assert orchestrator.client.condition_store.path == (
            tmp_path / "data" / "snapshots" / "http_cache.json"
        ).resolve()
    finally:
        orchestrator.close()


def test_dry_run_plans_without_calling_network(tmp_path, monkeypatch) -> None:
    client = _offline_client()

    def forbid_network(*args, **kwargs):
        raise AssertionError("dry-run attempted network access")

    monkeypatch.setattr(client, "get", forbid_network)
    orchestrator = CrawlOrchestrator.from_yaml(
        PROJECT_ROOT / "config" / "competitors.yaml",
        tmp_path / "data",
        client=client,
    )

    summary = orchestrator.run(
        competitors="cursor",
        sources="official,changelog,pricing",
        dry_run=True,
        crawl_run_id="dry-run",
    )

    assert summary.exit_code == 0
    assert summary.dry_run is True
    assert len(summary.planned_tasks) == 3
    assert summary.results == []
    assert not (tmp_path / "data" / "raw").exists()


def test_unknown_selection_is_configuration_error_without_network(
    tmp_path,
    monkeypatch,
) -> None:
    client = _offline_client()
    monkeypatch.setattr(
        client,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid config attempted network access")
        ),
    )
    orchestrator = CrawlOrchestrator.from_yaml(
        PROJECT_ROOT / "config" / "competitors.yaml",
        tmp_path / "data",
        client=client,
    )

    summary = orchestrator.run(
        competitors="missing_product",
        sources="unknown_source",
    )

    assert summary.exit_code == 1
    assert "unknown competitor: missing_product" in summary.configuration_errors
    assert "unknown source: unknown_source" in summary.configuration_errors


def test_enabled_sources_without_targets_are_configuration_errors(tmp_path) -> None:
    config = {
        "competitors": [
            {
                "id": "cursor",
                "sources": {
                    "official": {"enabled": True, "urls": []},
                    "github": {"enabled": True, "repositories": []},
                },
            }
        ]
    }
    orchestrator = CrawlOrchestrator(
        config,
        tmp_path / "data",
        client=_offline_client(),
    )

    summary = orchestrator.run(dry_run=True)

    assert summary.planned_tasks == []
    assert summary.exit_code == 1
    assert summary.configuration_errors == [
        "cursor/official: enabled source has no URL",
        "cursor/github: enabled source has no repository",
    ]


@responses.activate
def test_orchestrator_continues_after_one_source_timeout(tmp_path, fixture_text) -> None:
    config = {
        "defaults": {
            "max_retries": 0,
            "requests_per_second_per_domain": 0,
            "minimum_visible_text_chars": 40,
        },
        "competitors": [
            {
                "id": "cursor",
                "name": "Cursor",
                "sources": {
                    "official": {
                        "enabled": True,
                        "urls": ["https://example.test/official"],
                    },
                    "pricing": {
                        "enabled": True,
                        "urls": ["https://example.test/pricing"],
                    },
                },
            }
        ],
    }
    responses.add(
        responses.GET,
        "https://example.test/official",
        body=requests.Timeout("official timed out"),
    )
    responses.add(
        responses.GET,
        "https://example.test/pricing",
        status=200,
        body=fixture_text("pages/pricing.html"),
        content_type="text/html",
    )
    orchestrator = CrawlOrchestrator(
        config,
        tmp_path / "data",
        client=_offline_client(),
    )

    summary = orchestrator.run(crawl_run_id="run-001")

    assert summary.exit_code == 2
    assert len(summary.results) == 2
    assert summary.results[0].failure_count == 1
    assert summary.results[1].success_count == 1
    meta_files = list((tmp_path / "data" / "raw").rglob("*.meta.json"))
    assert len(meta_files) == 2


@responses.activate
def test_generic_page_source_is_persisted_under_its_own_category(
    tmp_path,
    fixture_text,
) -> None:
    config = {
        "defaults": {
            "max_retries": 0,
            "requests_per_second_per_domain": 0,
            "minimum_visible_text_chars": 40,
        },
        "competitors": [
            {
                "id": "cursor",
                "sources": {
                    "product_docs": {
                        "enabled": True,
                        "evidence_level": "A",
                        "urls": ["https://example.test/docs"],
                    }
                },
            }
        ],
    }
    responses.add(
        responses.GET,
        "https://example.test/docs",
        status=200,
        body=fixture_text("pages/official.html"),
        content_type="text/html",
    )
    orchestrator = CrawlOrchestrator(
        config,
        tmp_path / "data",
        client=_offline_client(),
    )

    summary = orchestrator.run(crawl_run_id="run-docs")

    assert summary.exit_code == 0
    assert summary.results[0].source_type is SourceType.PRODUCT_DOCS
    assert summary.results[0].records[0].source_type is SourceType.PRODUCT_DOCS
    assert len(list((tmp_path / "data" / "raw" / "cursor" / "product_docs").rglob("*.html"))) == 1


@responses.activate
def test_generic_page_source_can_use_structured_endpoint_and_public_url(
    tmp_path,
) -> None:
    public_url = "https://example.test/privacy"
    api_url = "https://api.example.test/terms"
    config = {
        "defaults": {
            "max_retries": 0,
            "requests_per_second_per_domain": 0,
        },
        "competitors": [
            {
                "id": "trae",
                "sources": {
                    "security_privacy": {
                        "enabled": True,
                        "urls": [public_url],
                        "request_overrides": {
                            public_url: {
                                "url": api_url,
                                "method": "POST",
                                "params": {"Language": "EN"},
                                "json": {"termsType": "privacy"},
                            }
                        },
                    }
                },
            }
        ],
    }
    responses.add(
        responses.POST,
        f"{api_url}?Language=EN",
        status=200,
        json={"Result": {"Title": "Privacy", "Content": "Policy body"}},
    )
    orchestrator = CrawlOrchestrator(
        config,
        tmp_path / "data",
        client=_offline_client(),
    )

    summary = orchestrator.run(crawl_run_id="run-api")

    [record] = summary.results[0].records
    assert summary.exit_code == 0
    assert record.requested_url == public_url
    assert record.canonical_url == public_url
    assert record.final_url == f"{api_url}?Language=EN"
    assert record.content_type == "application/json"
    assert record.source_metadata["acquisition_method"] == "POST"
    assert record.source_metadata["acquisition_url"] == api_url


@responses.activate
def test_supported_embedded_payload_does_not_require_browser(tmp_path) -> None:
    url = "https://example.test/docs"
    config = {
        "defaults": {
            "max_retries": 0,
            "requests_per_second_per_domain": 0,
            "minimum_visible_text_chars": 200,
        },
        "competitors": [
            {
                "id": "cursor",
                "sources": {
                    "product_docs": {
                        "enabled": True,
                        "embedded_content": "cursor_next_rsc",
                        "urls": [url],
                    }
                },
            }
        ],
    }
    responses.add(
        responses.GET,
        url,
        status=200,
        body="<html><script>self.__next_f.push([1,\"payload\"])</script></html>",
        content_type="text/html",
    )
    orchestrator = CrawlOrchestrator(
        config,
        tmp_path / "data",
        client=_offline_client(),
    )

    summary = orchestrator.run(crawl_run_id="run-embedded")

    [record] = summary.results[0].records
    assert record.needs_browser is False
    assert record.source_metadata["embedded_content_supported"] is True


@responses.activate
def test_orchestrator_returns_runtime_error_when_every_source_fails(tmp_path) -> None:
    config = {
        "defaults": {"max_retries": 0, "requests_per_second_per_domain": 0},
        "competitors": [
            {
                "id": "cursor",
                "name": "Cursor",
                "sources": {
                    "official": {
                        "enabled": True,
                        "urls": ["https://example.test/official"],
                    }
                },
            }
        ],
    }
    responses.add(
        responses.GET,
        "https://example.test/official",
        body=requests.Timeout("source timed out"),
    )
    orchestrator = CrawlOrchestrator(
        config,
        tmp_path / "data",
        client=_offline_client(),
    )

    summary = orchestrator.run(crawl_run_id="run-all-failed")

    assert summary.success_count == 0
    assert summary.unchanged_count == 0
    assert summary.failure_count == 1
    assert summary.exit_code == 1
