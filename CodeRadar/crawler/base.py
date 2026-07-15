"""Base collector implementation and shared page helpers."""

from __future__ import annotations

import html
import logging
import re
from abc import ABC, abstractmethod
from typing import Mapping

import requests

from .http_client import HttpClient, RobotsDeniedError
from .models import CollectorResult, CollectorTask, SourceType
from .storage import RawWriter


LOGGER = logging.getLogger(__name__)
_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1>",
    flags=re.IGNORECASE | re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def visible_text_length(payload: bytes, encoding: str = "utf-8") -> int:
    """Estimate server-rendered visible text without performing data cleaning."""

    text = payload.decode(encoding or "utf-8", errors="replace")
    text = _SCRIPT_STYLE.sub(" ", text)
    text = _TAG.sub(" ", text)
    return len(_SPACE.sub(" ", html.unescape(text)).strip())


class BaseCollector(ABC):
    """Collector contract used by the orchestration layer."""

    source_type: SourceType

    def __init__(self, client: HttpClient, writer: RawWriter) -> None:
        self.client = client
        self.writer = writer

    @abstractmethod
    def collect(self, task: CollectorTask) -> CollectorResult:
        """Collect a task without raising for source-local failures."""

    def _error_record(
        self,
        result: CollectorResult,
        task: CollectorTask,
        url: str,
        error: Exception | str,
        *,
        source_type: SourceType | None = None,
        response: requests.Response | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        message = str(error)
        record = self.writer.write_error(
            competitor=task.competitor,
            source_type=source_type or self.source_type,
            requested_url=url,
            final_url=response.url if response is not None else None,
            http_status=response.status_code if response is not None else None,
            error=message,
            source_metadata=metadata or task.metadata,
        )
        result.add_error(f"{url}: {message}", record)


class PageCollector(BaseCollector):
    """Shared acquisition behavior for official and pricing pages."""

    minimum_visible_characters = 80

    def collect(self, task: CollectorTask) -> CollectorResult:
        result = CollectorResult(task.competitor, self.source_type)
        if not task.enabled:
            result.skipped.append("source disabled")
            return result
        if not task.urls:
            result.skipped.append("no URL configured")
            return result

        for url in task.urls:
            try:
                response = self.client.get(url, force=task.force)
            except (requests.RequestException, RobotsDeniedError) as exc:
                self._error_record(result, task, url, exc)
                continue

            if response.status_code == 304:
                result.records.append(
                    self.writer.write_response(
                        competitor=task.competitor,
                        source_type=self.source_type,
                        requested_url=url,
                        response=response,
                        source_metadata=task.metadata,
                    )
                )
                continue
            if not 200 <= response.status_code < 300:
                self._error_record(
                    result,
                    task,
                    url,
                    f"HTTP {response.status_code}",
                    response=response,
                )
                continue

            content_type = response.headers.get("Content-Type", "").lower()
            needs_browser = "html" in content_type and visible_text_length(
                response.content, response.encoding or "utf-8"
            ) < self.minimum_visible_characters
            metadata = dict(task.metadata)
            metadata.update(
                {
                    "format": task.format,
                    "visible_text_length": visible_text_length(
                        response.content, response.encoding or "utf-8"
                    )
                    if "html" in content_type
                    else None,
                }
            )
            result.records.append(
                self.writer.write_response(
                    competitor=task.competitor,
                    source_type=self.source_type,
                    requested_url=url,
                    response=response,
                    needs_browser=needs_browser,
                    source_metadata=metadata,
                )
            )
        return result
