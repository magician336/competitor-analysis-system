"""Base collector implementation and shared page helpers."""

from __future__ import annotations

import html
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Mapping

import requests

from .browser_renderer import BrowserRenderer
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
_SUPPORTED_EMBEDDED_MARKERS = {
    "cursor_next_rsc": b"self.__next_f.push",
    "trae_router_document": b"window._ROUTER_DATA",
}


def visible_text_length(payload: bytes, encoding: str = "utf-8") -> int:
    """Estimate server-rendered visible text without performing data cleaning."""

    text = payload.decode(encoding or "utf-8", errors="replace")
    text = _SCRIPT_STYLE.sub(" ", text)
    text = _TAG.sub(" ", text)
    return len(_SPACE.sub(" ", html.unescape(text)).strip())


class BaseCollector(ABC):
    """Collector contract used by the orchestration layer."""

    source_type: SourceType

    def __init__(
        self,
        client: HttpClient,
        writer: RawWriter,
        *,
        browser_renderer: BrowserRenderer | None = None,
    ) -> None:
        self.client = client
        self.writer = writer
        self.browser_renderer = browser_renderer

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

    @staticmethod
    def _browser_fallback_settings(task: CollectorTask) -> Mapping[str, Any]:
        value = task.metadata.get("browser_fallback")
        if value is True:
            return {"enabled": True}
        if isinstance(value, Mapping):
            return value
        return {}

    def _render_with_browser(
        self,
        task: CollectorTask,
        public_url: str,
        response: requests.Response,
        visible_length: int,
        metadata: dict[str, Any],
    ) -> tuple[requests.Response, int]:
        settings = self._browser_fallback_settings(task)
        browser_minimum = int(
            settings.get(
                "minimum_text_characters",
                self.minimum_visible_characters,
            )
        )
        if not settings.get("enabled") or visible_length >= browser_minimum:
            return response, visible_length
        if self.browser_renderer is None:
            metadata["browser_fallback_error"] = "browser renderer is unavailable"
            return response, visible_length

        try:
            rendered = self.browser_renderer.render(
                public_url,
                timeout_seconds=float(settings.get("timeout_seconds", 30)),
                wait_selector=(
                    str(settings["wait_selector"]).strip()
                    if settings.get("wait_selector")
                    else None
                ),
                minimum_text_characters=int(
                    settings.get(
                        "minimum_text_characters",
                        self.minimum_visible_characters,
                    )
                ),
                settle_milliseconds=int(settings.get("settle_milliseconds", 1_000)),
            )
        except Exception as exc:
            metadata["browser_fallback_error"] = str(exc)
            return response, visible_length

        rendered_response = requests.Response()
        rendered_response.status_code = rendered.http_status or response.status_code
        rendered_response.url = rendered.final_url or public_url
        rendered_response.headers["Content-Type"] = "text/html; charset=utf-8"
        rendered_response.encoding = "utf-8"
        rendered_response._content = rendered.html.encode("utf-8")
        rendered_length = visible_text_length(rendered_response.content, "utf-8")
        metadata.update(
            {
                "browser_rendered": True,
                "browser_static_visible_text_length": visible_length,
                "browser_rendered_visible_text_length": rendered_length,
            }
        )
        return rendered_response, rendered_length

    @staticmethod
    def _request_override(
        task: CollectorTask,
        public_url: str,
    ) -> Mapping[str, Any]:
        overrides = task.metadata.get("request_overrides", {})
        if not isinstance(overrides, Mapping):
            return {}
        value = overrides.get(public_url, {})
        return value if isinstance(value, Mapping) else {}

    def _fetch(
        self,
        task: CollectorTask,
        public_url: str,
    ) -> tuple[requests.Response, str, str]:
        override = self._request_override(task, public_url)
        acquisition_url = str(override.get("url") or public_url)
        method = str(override.get("method") or "GET").strip().upper()
        params = override.get("params")
        headers = override.get("headers")
        if method == "GET":
            response = self.client.get(
                acquisition_url,
                params=params if isinstance(params, Mapping) else None,
                headers=headers if isinstance(headers, Mapping) else None,
                force=task.force,
            )
        elif method == "POST":
            json_body = override.get("json")
            form_data = override.get("data")
            response = self.client.post(
                acquisition_url,
                params=params if isinstance(params, Mapping) else None,
                json_body=json_body,
                data=form_data if isinstance(form_data, Mapping) else None,
                headers=headers if isinstance(headers, Mapping) else None,
            )
        else:
            raise ValueError(
                f"unsupported acquisition method {method!r} for {public_url}"
            )
        return response, acquisition_url, method

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
                response, acquisition_url, method = self._fetch(task, url)
            except (requests.RequestException, RobotsDeniedError) as exc:
                self._error_record(result, task, url, exc)
                continue
            except ValueError as exc:
                self._error_record(result, task, url, exc)
                continue

            metadata = dict(task.metadata)
            metadata.update(
                {
                    "acquisition_url": acquisition_url,
                    "acquisition_method": method,
                }
            )

            if response.status_code == 304:
                result.records.append(
                    self.writer.write_response(
                        competitor=task.competitor,
                        source_type=self.source_type,
                        requested_url=url,
                        response=response,
                        canonical_url=url,
                        source_metadata=metadata,
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
            visible_length = (
                visible_text_length(response.content, response.encoding or "utf-8")
                if "html" in content_type
                else None
            )
            if visible_length is not None:
                response, visible_length = self._render_with_browser(
                    task,
                    url,
                    response,
                    visible_length,
                    metadata,
                )
                content_type = response.headers.get("Content-Type", "").lower()
            embedded_mode = str(task.metadata.get("embedded_content") or "")
            embedded_marker = _SUPPORTED_EMBEDDED_MARKERS.get(embedded_mode)
            embedded_supported = bool(
                embedded_marker and embedded_marker in response.content
            )
            needs_browser = bool(
                "html" in content_type
                and visible_length is not None
                and (
                    visible_length < self.minimum_visible_characters
                    or "browser_fallback_error" in metadata
                )
                and not embedded_supported
            )
            metadata.update(
                {
                    "format": task.format,
                    "visible_text_length": visible_length,
                    "embedded_content_supported": embedded_supported,
                }
            )
            result.records.append(
                self.writer.write_response(
                    competitor=task.competitor,
                    source_type=self.source_type,
                    requested_url=url,
                    response=response,
                    canonical_url=url,
                    needs_browser=needs_browser,
                    source_metadata=metadata,
                )
            )
        return result


class ConfiguredPageCollector(PageCollector):
    """Collect a configured static page under its explicit source category."""

    def __init__(
        self,
        client: HttpClient,
        writer: RawWriter,
        source_type: SourceType | str,
        *,
        browser_renderer: BrowserRenderer | None = None,
    ) -> None:
        super().__init__(
            client,
            writer,
            browser_renderer=browser_renderer,
        )
        self.source_type = SourceType.parse(source_type)
