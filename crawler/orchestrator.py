"""Configuration-driven orchestration for raw data acquisition."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .base import ConfiguredPageCollector
from .browser_renderer import PlaywrightBrowserRenderer
from .crawl_changelog import ChangelogCollector
from .crawl_github import GitHubCollector
from .crawl_official import OfficialCollector
from .crawl_pricing import PricingCollector
from .http_client import ConditionalRequestStore, HttpClient, HttpClientConfig
from .models import (
    CollectorResult,
    CollectorTask,
    CrawlSummary,
    SourceType,
    utc_now,
)
from .storage import RawWriter


LOGGER = logging.getLogger(__name__)
_TASK_SOURCES = {
    SourceType.OFFICIAL,
    SourceType.CHANGELOG,
    SourceType.PRICING,
    SourceType.PRODUCT_DOCS,
    SourceType.STATUS_PAGE,
    SourceType.GITHUB,
    SourceType.PLUGIN_MARKETPLACE,
    SourceType.COMMUNITY,
    SourceType.REVIEW,
    SourceType.SECURITY_PRIVACY,
    SourceType.BENCHMARK,
}

_GENERIC_PAGE_SOURCES = _TASK_SOURCES - {
    SourceType.OFFICIAL,
    SourceType.CHANGELOG,
    SourceType.PRICING,
    SourceType.GITHUB,
}


def _selection(values: str | Iterable[str] | None) -> set[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        raw = values.split(",")
    else:
        raw = list(values)
    normalized = {str(value).strip().lower() for value in raw if str(value).strip()}
    return None if not normalized or "all" in normalized else normalized


def _competitor_items(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = config.get("competitors", [])
    if isinstance(raw, Mapping):
        result = []
        for competitor_id, value in raw.items():
            item = dict(value) if isinstance(value, Mapping) else {}
            item.setdefault("id", str(competitor_id))
            result.append(item)
        return result
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    return []


class CrawlOrchestrator:
    """Build collector tasks from YAML and execute each source independently."""

    def __init__(
        self,
        config: Mapping[str, Any],
        data_root: str | Path,
        *,
        github_token: str | None = None,
        client: HttpClient | None = None,
        client_config: HttpClientConfig | None = None,
    ) -> None:
        self.config = dict(config)
        candidate = Path(data_root).resolve()
        if candidate.name.lower() == "raw":
            self.raw_root = candidate
            self.snapshot_root = candidate.parent / "snapshots"
        else:
            self.raw_root = candidate / "raw"
            self.snapshot_root = candidate / "snapshots"
        self.github_token = github_token or os.getenv("GITHUB_TOKEN")
        defaults = self.config.get("defaults", {})
        defaults = defaults if isinstance(defaults, Mapping) else {}
        self.defaults = dict(defaults)
        if client is None:
            config_from_yaml = client_config or HttpClientConfig(
                timeout_seconds=float(defaults.get("request_timeout_seconds", 15)),
                retries=int(defaults.get("max_retries", 3)),
                requests_per_second=float(
                    defaults.get("requests_per_second_per_domain", 1.0)
                ),
            )
            client = HttpClient(
                config_from_yaml,
                condition_store=ConditionalRequestStore(
                    self.snapshot_root / "http_cache.json"
                ),
            )
        self.client = client
        self.browser_renderer = PlaywrightBrowserRenderer()

    @classmethod
    def from_yaml(
        cls,
        config_path: str | Path,
        data_root: str | Path,
        **kwargs: Any,
    ) -> "CrawlOrchestrator":
        path = Path(config_path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError(f"crawler configuration must be a mapping: {path}")
        return cls(raw, data_root, **kwargs)

    def build_tasks(
        self,
        *,
        competitors: str | Iterable[str] | None = None,
        sources: str | Iterable[str] | None = None,
        since_days: int | None = None,
        max_issues: int | None = None,
        max_comments: int | None = None,
        force: bool = False,
    ) -> tuple[list[CollectorTask], list[str]]:
        selected_competitors = _selection(competitors)
        selected_sources = _selection(sources)
        github_selection: set[str] | None = None
        errors: list[str] = []
        if selected_sources:
            normalized_sources: set[str] = set()
            github_selection = set()
            for source in selected_sources:
                try:
                    parsed = SourceType.parse(source)
                except ValueError:
                    errors.append(f"unknown source: {source}")
                    continue
                if parsed in {
                    SourceType.GITHUB,
                    SourceType.GITHUB_RELEASE,
                    SourceType.GITHUB_ISSUE,
                }:
                    github_selection.add(parsed.value)
                if parsed in {SourceType.GITHUB_RELEASE, SourceType.GITHUB_ISSUE}:
                    parsed = SourceType.GITHUB
                if parsed not in _TASK_SOURCES:
                    errors.append(f"unsupported task source: {source}")
                    continue
                normalized_sources.add(parsed.value)
            selected_sources = normalized_sources

        days = int(
            since_days
            if since_days is not None
            else self.defaults.get("since_days", 90)
        )
        if days < 0:
            errors.append("since_days must be non-negative")
            days = 0
        since = utc_now() - timedelta(days=days)
        issue_limit = int(
            max_issues
            if max_issues is not None
            else self.defaults.get("max_issues", 100)
        )
        comment_limit = int(
            max_comments
            if max_comments is not None
            else self.defaults.get("max_comments", 20)
        )

        items = _competitor_items(self.config)
        configured_ids = {
            str(item.get("id", "")).strip().lower() for item in items if item.get("id")
        }
        if selected_competitors:
            for missing in sorted(selected_competitors - configured_ids):
                errors.append(f"unknown competitor: {missing}")

        tasks: list[CollectorTask] = []
        for item in items:
            competitor_id = str(item.get("id") or "").strip()
            if not competitor_id:
                errors.append("competitor entry is missing id")
                continue
            if selected_competitors and competitor_id.lower() not in selected_competitors:
                continue
            raw_sources = item.get("sources", {})
            if not isinstance(raw_sources, Mapping):
                errors.append(f"{competitor_id}: sources must be a mapping")
                continue
            for raw_source_type, raw_source_config in raw_sources.items():
                try:
                    source_type = SourceType.parse(str(raw_source_type))
                except ValueError:
                    errors.append(
                        f"{competitor_id}: unknown source {raw_source_type}"
                    )
                    continue
                if source_type in {SourceType.GITHUB_RELEASE, SourceType.GITHUB_ISSUE}:
                    source_type = SourceType.GITHUB
                if source_type not in _TASK_SOURCES:
                    continue
                if selected_sources and source_type.value not in selected_sources:
                    continue
                if not isinstance(raw_source_config, (Mapping, str, Sequence)):
                    errors.append(
                        f"{competitor_id}/{source_type.value}: invalid source configuration"
                    )
                    continue
                try:
                    task = CollectorTask.from_mapping(
                        competitor_id,
                        source_type,
                        raw_source_config,
                        since=since,
                        max_issues=issue_limit,
                        max_comments=comment_limit,
                        force=force,
                    )
                except (TypeError, ValueError) as exc:
                    errors.append(f"{competitor_id}/{source_type.value}: {exc}")
                    continue
                if not task.enabled:
                    continue
                if source_type is SourceType.GITHUB and not task.repositories:
                    errors.append(
                        f"{competitor_id}/{source_type.value}: enabled source has no repository"
                    )
                    continue
                if source_type is not SourceType.GITHUB and not task.urls:
                    errors.append(
                        f"{competitor_id}/{source_type.value}: enabled source has no URL"
                    )
                    continue
                metadata = dict(task.metadata)
                if source_type is SourceType.GITHUB and github_selection:
                    collect_all_github = SourceType.GITHUB.value in github_selection
                    if collect_all_github:
                        metadata["collect_releases"] = bool(
                            metadata.get("collect_releases", True)
                        )
                        metadata["collect_issues"] = bool(
                            metadata.get("collect_issues", True)
                        )
                    else:
                        metadata["collect_releases"] = (
                            SourceType.GITHUB_RELEASE.value in github_selection
                        )
                        metadata["collect_issues"] = (
                            SourceType.GITHUB_ISSUE.value in github_selection
                        )
                metadata.update(
                    {
                        "competitor_name": item.get("name", competitor_id),
                        "competitor_aliases": item.get("aliases", []),
                    }
                )
                tasks.append(
                    CollectorTask(
                        competitor=task.competitor,
                        source_type=task.source_type,
                        urls=task.urls,
                        repositories=task.repositories,
                        enabled=task.enabled,
                        format=task.format,
                        since=task.since,
                        max_issues=task.max_issues,
                        max_comments=task.max_comments,
                        force=task.force,
                        metadata=metadata,
                    )
                )
        return tasks, errors

    def run(
        self,
        *,
        competitors: str | Iterable[str] | None = None,
        sources: str | Iterable[str] | None = None,
        since_days: int | None = None,
        max_issues: int | None = None,
        max_comments: int | None = None,
        dry_run: bool = False,
        force: bool = False,
        crawl_run_id: str | None = None,
    ) -> CrawlSummary:
        started_at = utc_now()
        run_id = crawl_run_id or (
            started_at.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        )
        tasks, errors = self.build_tasks(
            competitors=competitors,
            sources=sources,
            since_days=since_days,
            max_issues=max_issues,
            max_comments=max_comments,
            force=force,
        )
        summary = CrawlSummary(
            crawl_run_id=run_id,
            started_at=started_at,
            planned_tasks=tasks,
            configuration_errors=errors,
            dry_run=dry_run,
        )
        if dry_run or errors:
            summary.finished_at = utc_now()
            return summary

        writer = RawWriter(self.raw_root, run_id)
        collectors = {
            SourceType.OFFICIAL: OfficialCollector(
                self.client,
                writer,
                browser_renderer=self.browser_renderer,
            ),
            SourceType.CHANGELOG: ChangelogCollector(self.client, writer),
            SourceType.PRICING: PricingCollector(
                self.client,
                writer,
                browser_renderer=self.browser_renderer,
            ),
            SourceType.GITHUB: GitHubCollector(
                self.client, writer, token=self.github_token
            ),
            **{
                source_type: ConfiguredPageCollector(
                    self.client,
                    writer,
                    source_type,
                    browser_renderer=self.browser_renderer,
                )
                for source_type in _GENERIC_PAGE_SOURCES
            },
        }
        minimum_visible = int(
            self.defaults.get("minimum_visible_text_chars", 80)
        )
        for collector in collectors.values():
            if hasattr(collector, "minimum_visible_characters"):
                collector.minimum_visible_characters = minimum_visible

        for task in tasks:
            collector = collectors[task.source_type]
            LOGGER.info("Collecting %s/%s", task.competitor, task.source_type.value)
            try:
                result = collector.collect(task)
            except Exception as exc:  # Guard the cross-source isolation boundary.
                LOGGER.exception(
                    "Collector failed unexpectedly for %s/%s",
                    task.competitor,
                    task.source_type.value,
                )
                result = CollectorResult(task.competitor, task.source_type)
                result.errors.append(f"unexpected collector error: {exc}")
            summary.results.append(result)
        summary.finished_at = utc_now()
        return summary

    def close(self) -> None:
        self.browser_renderer.close()
        self.client.close()

    def __enter__(self) -> "CrawlOrchestrator":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
