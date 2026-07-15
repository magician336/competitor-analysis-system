"""GitHub Release and Issue collector using the public REST API."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

import requests
from dateutil import parser as date_parser

from .base import BaseCollector
from .http_client import RobotsDeniedError
from .models import CollectorResult, CollectorTask, SourceType


LOGGER = logging.getLogger(__name__)


def _github_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        sections = [section.strip() for section in part.split(";")]
        if len(sections) < 2 or 'rel="next"' not in sections[1:]:
            continue
        target = sections[0]
        if target.startswith("<") and target.endswith(">"):
            return target[1:-1]
    return None


class GitHubCollector(BaseCollector):
    """Persist one composite raw record per release or issue."""

    source_type = SourceType.GITHUB
    api_root = "https://api.github.com"

    def __init__(self, client, writer, *, token: str | None = None) -> None:
        super().__init__(client, writer)
        self.token = token.strip() if token else None

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self,
        url: str,
        *,
        task: CollectorTask,
        params: Mapping[str, Any] | None = None,
        conditional: bool = True,
    ) -> requests.Response:
        return self.client.get(
            url,
            params=params,
            headers=self.headers,
            conditional=conditional,
            force=task.force,
        )

    def _write_http_error(
        self,
        result: CollectorResult,
        task: CollectorTask,
        url: str,
        source_type: SourceType,
        response: requests.Response,
        metadata: Mapping[str, Any],
    ) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        suffix = ""
        if remaining == "0":
            suffix = f"; GitHub rate limit exhausted (reset={reset or 'unknown'})"
        self._error_record(
            result,
            task,
            url,
            f"HTTP {response.status_code}{suffix}",
            source_type=source_type,
            response=response,
            metadata=metadata,
        )

    def _collect_releases(
        self,
        task: CollectorTask,
        repository: str,
        result: CollectorResult,
    ) -> None:
        first_url = f"{self.api_root}/repos/{repository}/releases"
        url: str | None = first_url
        params: Mapping[str, Any] | None = {"per_page": 100, "page": 1}
        while url:
            try:
                response = self._request(url, task=task, params=params)
            except (requests.RequestException, RobotsDeniedError) as exc:
                self._error_record(
                    result,
                    task,
                    url,
                    exc,
                    source_type=SourceType.GITHUB_RELEASE,
                    metadata={**task.metadata, "repository": repository},
                )
                return
            params = None  # Subsequent Link URLs already include their query string.
            if response.status_code == 304:
                result.records.append(
                    self.writer.write_response(
                        competitor=task.competitor,
                        source_type=SourceType.GITHUB_RELEASE,
                        requested_url=url,
                        response=response,
                        source_metadata={
                            **task.metadata,
                            "repository": repository,
                            "not_modified_page": True,
                        },
                    )
                )
                return
            if not 200 <= response.status_code < 300:
                self._write_http_error(
                    result,
                    task,
                    url,
                    SourceType.GITHUB_RELEASE,
                    response,
                    {**task.metadata, "repository": repository},
                )
                return
            try:
                items = response.json()
            except requests.JSONDecodeError as exc:
                self._error_record(
                    result,
                    task,
                    url,
                    f"invalid GitHub JSON: {exc}",
                    source_type=SourceType.GITHUB_RELEASE,
                    response=response,
                    metadata={**task.metadata, "repository": repository},
                )
                return
            if not isinstance(items, list):
                self._error_record(
                    result,
                    task,
                    url,
                    "GitHub releases response is not a list",
                    source_type=SourceType.GITHUB_RELEASE,
                    response=response,
                    metadata={**task.metadata, "repository": repository},
                )
                return

            reached_cutoff = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                published_at = _github_datetime(
                    item.get("published_at") or item.get("created_at")
                )
                if task.since and published_at and published_at < task.since:
                    reached_cutoff = True
                    continue
                canonical_url = str(
                    item.get("html_url")
                    or item.get("url")
                    or f"{first_url}/{item.get('id', 'unknown')}"
                )
                result.records.append(
                    self.writer.write_json(
                        competitor=task.competitor,
                        source_type=SourceType.GITHUB_RELEASE,
                        requested_url=url,
                        canonical_url=canonical_url,
                        final_url=response.url,
                        http_status=response.status_code,
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                        published_at=published_at,
                        payload={
                            "kind": "github_release",
                            "repository": repository,
                            "item": item,
                        },
                        source_metadata={
                            **task.metadata,
                            "repository": repository,
                            "release_id": item.get("id"),
                            "tag_name": item.get("tag_name"),
                        },
                    )
                )
            if reached_cutoff or not items:
                return
            url = _next_link(response.headers.get("Link"))

    def _collect_comments(
        self,
        task: CollectorTask,
        repository: str,
        issue_number: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if task.max_comments == 0:
            return [], None
        url: str | None = (
            f"{self.api_root}/repos/{repository}/issues/{issue_number}/comments"
        )
        params: Mapping[str, Any] | None = {
            "per_page": min(task.max_comments, 100),
            "page": 1,
        }
        comments: list[dict[str, Any]] = []
        while url and len(comments) < task.max_comments:
            try:
                # Comments are embedded in the issue payload. Conditional 304
                # responses cannot be reused safely without a payload cache, so
                # this endpoint always returns a complete list.
                response = self._request(
                    url, task=task, params=params, conditional=False
                )
            except (requests.RequestException, RobotsDeniedError) as exc:
                return comments, str(exc)
            params = None
            if response.status_code == 304:
                return comments, "comments endpoint returned 304 without a cached payload"
            if not 200 <= response.status_code < 300:
                return comments, f"HTTP {response.status_code}"
            try:
                items = response.json()
            except requests.JSONDecodeError as exc:
                return comments, f"invalid GitHub JSON: {exc}"
            if not isinstance(items, list):
                return comments, "GitHub comments response is not a list"
            comments.extend(item for item in items if isinstance(item, dict))
            url = _next_link(response.headers.get("Link"))
        return comments[: task.max_comments], None

    def _collect_issues(
        self,
        task: CollectorTask,
        repository: str,
        result: CollectorResult,
    ) -> None:
        first_url = f"{self.api_root}/repos/{repository}/issues"
        url: str | None = first_url
        params: Mapping[str, Any] | None = {
            "state": "all",
            "sort": "updated",
            "direction": "desc",
            "per_page": min(max(task.max_issues, 1), 100),
            "page": 1,
        }
        if task.since:
            params["since"] = task.since.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
        collected = 0
        while url and collected < task.max_issues:
            try:
                response = self._request(url, task=task, params=params)
            except (requests.RequestException, RobotsDeniedError) as exc:
                self._error_record(
                    result,
                    task,
                    url,
                    exc,
                    source_type=SourceType.GITHUB_ISSUE,
                    metadata={**task.metadata, "repository": repository},
                )
                return
            params = None
            if response.status_code == 304:
                result.records.append(
                    self.writer.write_response(
                        competitor=task.competitor,
                        source_type=SourceType.GITHUB_ISSUE,
                        requested_url=url,
                        response=response,
                        source_metadata={
                            **task.metadata,
                            "repository": repository,
                            "not_modified_page": True,
                        },
                    )
                )
                return
            if not 200 <= response.status_code < 300:
                self._write_http_error(
                    result,
                    task,
                    url,
                    SourceType.GITHUB_ISSUE,
                    response,
                    {**task.metadata, "repository": repository},
                )
                return
            try:
                items = response.json()
            except requests.JSONDecodeError as exc:
                self._error_record(
                    result,
                    task,
                    url,
                    f"invalid GitHub JSON: {exc}",
                    source_type=SourceType.GITHUB_ISSUE,
                    response=response,
                    metadata={**task.metadata, "repository": repository},
                )
                return
            if not isinstance(items, list):
                self._error_record(
                    result,
                    task,
                    url,
                    "GitHub issues response is not a list",
                    source_type=SourceType.GITHUB_ISSUE,
                    response=response,
                    metadata={**task.metadata, "repository": repository},
                )
                return

            for item in items:
                if collected >= task.max_issues:
                    break
                if not isinstance(item, dict) or "pull_request" in item:
                    continue
                issue_number = item.get("number")
                if not isinstance(issue_number, int):
                    continue
                declared_comments = item.get("comments")
                if isinstance(declared_comments, int) and declared_comments == 0:
                    comments, comments_error = [], None
                else:
                    comments, comments_error = self._collect_comments(
                        task, repository, issue_number
                    )
                published_at = _github_datetime(item.get("created_at"))
                canonical_url = str(
                    item.get("html_url") or item.get("url") or f"{first_url}/{issue_number}"
                )
                result.records.append(
                    self.writer.write_json(
                        competitor=task.competitor,
                        source_type=SourceType.GITHUB_ISSUE,
                        requested_url=url,
                        canonical_url=canonical_url,
                        final_url=response.url,
                        http_status=response.status_code,
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                        published_at=published_at,
                        payload={
                            "kind": "github_issue",
                            "repository": repository,
                            "item": item,
                            "comments": comments,
                            **(
                                {"comments_error": comments_error}
                                if comments_error
                                else {}
                            ),
                        },
                        source_metadata={
                            **task.metadata,
                            "repository": repository,
                            "issue_number": issue_number,
                            "state": item.get("state"),
                            "comments_collected": len(comments),
                            "comments_error": comments_error,
                        },
                    )
                )
                if comments_error:
                    result.errors.append(
                        f"{repository}#{issue_number} comments: {comments_error}"
                    )
                collected += 1
            if not items:
                return
            url = _next_link(response.headers.get("Link"))

    def collect(self, task: CollectorTask) -> CollectorResult:
        result = CollectorResult(task.competitor, SourceType.GITHUB)
        if not task.enabled:
            result.skipped.append("source disabled")
            return result
        if not task.repositories:
            result.skipped.append("no repository configured")
            return result

        collect_releases = bool(task.metadata.get("collect_releases", True))
        collect_issues = bool(task.metadata.get("collect_issues", True))
        for repository in task.repositories:
            if repository.count("/") != 1:
                result.errors.append(f"invalid GitHub repository: {repository}")
                continue
            if collect_releases:
                self._collect_releases(task, repository, result)
            if collect_issues:
                self._collect_issues(task, repository, result)
        return result


GitHubCrawler = GitHubCollector


__all__ = ["GitHubCollector", "GitHubCrawler"]
