"""Base collector implementation and shared page helpers."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

import requests

from .browser_renderer import BrowserRenderer
from .http_client import HttpClient, RobotsDeniedError
from .models import CollectorResult, CollectorTask, RawRecord, SourceType
from .page_discovery import LinkDiscoverySettings, discover_page_urls
from .sitemap_loader import SitemapDiscoverySettings, SitemapLoader
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
_ALLOWED_FINAL_ORIGINS_KEY = "_link_discovery_allowed_final_origins"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_SAME_ORIGIN_REDIRECTS = 5


def _http_origin(url: object) -> str | None:
    """Return a normalized HTTP origin, including its effective port."""

    try:
        parts = urlsplit(str(url or ""))
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    hostname = parts.hostname.rstrip(".").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    return f"{scheme}://{hostname}:{effective_port}"


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

    @classmethod
    def _response_metadata(
        cls,
        task: CollectorTask,
        public_url: str,
    ) -> dict[str, Any]:
        """Merge URL-specific extraction metadata over task-level metadata."""

        metadata = dict(task.metadata)
        metadata.pop(_ALLOWED_FINAL_ORIGINS_KEY, None)
        override = cls._request_override(task, public_url)
        override_metadata = override.get("source_metadata")
        if isinstance(override_metadata, Mapping):
            metadata.update(override_metadata)
        return metadata

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
            raw_allowed_origins = task.metadata.get(_ALLOWED_FINAL_ORIGINS_KEY)
            allowed_origins = (
                {str(value) for value in raw_allowed_origins}
                if isinstance(raw_allowed_origins, (list, tuple, set, frozenset))
                else set()
            )
            if allowed_origins:
                current_url = acquisition_url
                current_params = params if isinstance(params, Mapping) else None
                redirect_count = 0
                while True:
                    current_origin = _http_origin(current_url)
                    if current_origin not in allowed_origins:
                        raise ValueError(
                            "redirect target is outside configured "
                            f"link-discovery seed origins: {current_url}"
                        )
                    response = self.client.get(
                        current_url,
                        params=current_params,
                        headers=headers if isinstance(headers, Mapping) else None,
                        force=task.force,
                        allow_redirects=False,
                    )
                    location = response.headers.get("Location")
                    if (
                        response.status_code not in _REDIRECT_STATUSES
                        or not location
                    ):
                        break
                    if redirect_count >= _MAX_SAME_ORIGIN_REDIRECTS:
                        raise ValueError(
                            "link-discovery page exceeded the maximum of "
                            f"{_MAX_SAME_ORIGIN_REDIRECTS} same-origin redirects"
                        )
                    next_url = urljoin(
                        response.url or current_url,
                        str(location).strip(),
                    )
                    if _http_origin(next_url) not in allowed_origins:
                        raise ValueError(
                            "redirect target is outside configured "
                            f"link-discovery seed origins: {next_url}"
                        )
                    redirect_count += 1
                    current_url = next_url
                    current_params = None
            else:
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
            metadata = self._response_metadata(task, url)
            try:
                response, acquisition_url, method = self._fetch(task, url)
            except (requests.RequestException, RobotsDeniedError) as exc:
                self._error_record(result, task, url, exc, metadata=metadata)
                continue
            except ValueError as exc:
                self._error_record(result, task, url, exc, metadata=metadata)
                continue

            raw_allowed_origins = task.metadata.get(_ALLOWED_FINAL_ORIGINS_KEY)
            allowed_origins = (
                {str(value) for value in raw_allowed_origins}
                if isinstance(raw_allowed_origins, (list, tuple, set, frozenset))
                else set()
            )
            final_origin = _http_origin(response.url or acquisition_url)
            if allowed_origins and final_origin not in allowed_origins:
                self._error_record(
                    result,
                    task,
                    url,
                    "redirected outside configured link-discovery seed origins",
                    response=response,
                    metadata=metadata,
                )
                continue

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
                    metadata=metadata,
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
                final_origin = _http_origin(response.url or acquisition_url)
                if allowed_origins and final_origin not in allowed_origins:
                    self._error_record(
                        result,
                        task,
                        url,
                        "browser rendering left configured link-discovery seed origins",
                        response=response,
                        metadata=metadata,
                    )
                    continue
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
        self.sitemap_loader = SitemapLoader(client, writer, self.source_type)

    @staticmethod
    def _merge_result(
        target: CollectorResult,
        source: CollectorResult,
    ) -> None:
        target.records.extend(source.records)
        target.errors.extend(source.errors)
        target.skipped.extend(source.skipped)

    @staticmethod
    def _discovery_url_identity(
        value: object,
        settings: LinkDiscoverySettings,
    ) -> str | None:
        """Canonicalize one URL through the same rules used for discovered links."""

        raw = str(value or "").strip()
        if not raw:
            return None
        escaped = html.escape(raw, quote=True)
        urls = discover_page_urls(
            f'<a href="{escaped}"></a>',
            page_url=raw,
            query_params=settings.query_params,
            max_urls=1,
        )
        return urls[0] if urls else None

    def _verified_payload(self, record: RawRecord) -> bytes | None:
        """Read a raw payload only when its path and digest remain trustworthy."""

        if not record.payload_path or not record.payload_hash:
            return None
        raw_root = self.writer.raw_root.resolve()
        try:
            payload_path = (raw_root / record.payload_path).resolve()
            payload_path.relative_to(raw_root)
            payload = payload_path.read_bytes()
        except (OSError, ValueError):
            return None
        if hashlib.sha256(payload).hexdigest() != record.payload_hash:
            return None
        return payload

    def _historical_payload_index(
        self,
        task: CollectorTask,
        settings: LinkDiscoverySettings,
    ) -> dict[str, list[RawRecord]]:
        """Index prior successful payloads by canonical discovery URL."""

        indexed: dict[str, list[RawRecord]] = {}
        indexed_record_ids: dict[str, set[tuple[str, str]]] = {}
        for metadata_path in self.writer.raw_root.rglob("*.meta.json"):
            try:
                raw = json.loads(metadata_path.read_text(encoding="utf-8"))
                if (
                    not isinstance(raw, Mapping)
                    or raw.get("competitor") != task.competitor
                    or SourceType.parse(raw.get("source_type")) is not self.source_type
                ):
                    continue
                record = RawRecord.from_dict(raw)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                record.error
                or record.http_status is None
                or not 200 <= record.http_status < 300
                or record.http_status == 304
                or not record.payload_path
                or not record.payload_hash
            ):
                continue
            record_key = (record.crawl_run_id, record.raw_record_id)
            for candidate_url in (record.requested_url, record.canonical_url):
                identity = self._discovery_url_identity(candidate_url, settings)
                if identity is None:
                    continue
                known = indexed_record_ids.setdefault(identity, set())
                if record_key in known:
                    continue
                known.add(record_key)
                indexed.setdefault(identity, []).append(record)
        for records in indexed.values():
            records.sort(key=lambda item: item.fetched_at, reverse=True)
        return indexed

    def _payload_for_discovery(
        self,
        result: CollectorResult,
        parent: RawRecord,
        settings: LinkDiscoverySettings,
        seed_origins: set[str],
        historical_index: dict[str, list[RawRecord]] | None,
    ) -> tuple[bytes, str] | None:
        """Resolve a current or verified historical payload for link parsing."""

        if parent.http_status != 304:
            payload = self._verified_payload(parent)
            page_url = self._discovery_url_identity(
                parent.final_url or parent.canonical_url,
                settings,
            )
            if payload is None:
                result.errors.append(
                    f"{parent.canonical_url}: link discovery stopped: "
                    "current raw payload is missing or failed integrity verification"
                )
                return None
            if page_url is None or _http_origin(page_url) not in seed_origins:
                result.errors.append(
                    f"{parent.canonical_url}: link discovery stopped: "
                    "final URL is outside configured seed origins"
                )
                return None
            return payload, page_url

        identity = self._discovery_url_identity(
            parent.requested_url or parent.canonical_url,
            settings,
        )
        for historical in (historical_index or {}).get(identity or "", []):
            if historical.fetched_at > parent.fetched_at:
                continue
            page_url = self._discovery_url_identity(
                historical.final_url or historical.canonical_url,
                settings,
            )
            if page_url is None or _http_origin(page_url) not in seed_origins:
                continue
            payload = self._verified_payload(historical)
            if payload is not None:
                return payload, page_url
        result.errors.append(
            f"{parent.canonical_url}: link discovery stopped after HTTP 304: "
            "no verified historical raw payload is available"
        )
        return None

    def _collect_discovered_links(
        self,
        result: CollectorResult,
        task: CollectorTask,
        seed_records: list[RawRecord],
        settings: LinkDiscoverySettings,
    ) -> None:
        if not settings.enabled or settings.max_depth == 0 or settings.max_urls == 0:
            return

        seed_identities = {
            identity
            for url in task.urls
            if (identity := self._discovery_url_identity(url, settings))
        }
        seed_origins = {
            origin
            for identity in seed_identities
            if (origin := _http_origin(identity))
        }
        if not seed_origins:
            result.errors.append(
                "link discovery stopped: no valid HTTP(S) seed origin is configured"
            )
            return
        seen_urls = set(seed_identities)
        for record in result.records:
            for value in (
                record.requested_url,
                record.canonical_url,
                record.final_url,
            ):
                identity = self._discovery_url_identity(value, settings)
                if identity and _http_origin(identity) in seed_origins:
                    seen_urls.add(identity)
        frontier = [
            record
            for record in seed_records
            if record.error is None
            and (record.http_status == 304 or record.payload_path)
        ]
        discovered_count = 0
        historical_index: dict[str, list[RawRecord]] | None = None
        embedded_mode = (
            settings.embedded_mode
            or str(task.metadata.get("embedded_content") or "").strip()
            or None
        )

        for depth in range(1, settings.max_depth + 1):
            if not frontier or discovered_count >= settings.max_urls:
                break
            discovered_urls: list[str] = []
            for parent in frontier:
                if discovered_count + len(discovered_urls) >= settings.max_urls:
                    break
                try:
                    if parent.http_status == 304 and historical_index is None:
                        historical_index = self._historical_payload_index(
                            task,
                            settings,
                        )
                    resolved = self._payload_for_discovery(
                        result,
                        parent,
                        settings,
                        seed_origins,
                        historical_index,
                    )
                    if resolved is None:
                        continue
                    payload, page_url = resolved
                    page_urls = discover_page_urls(
                        payload,
                        page_url=page_url,
                        allowed_path_prefixes=settings.allowed_path_prefixes,
                        excluded_path_prefixes=settings.excluded_path_prefixes,
                        include_patterns=settings.include_patterns,
                        exclude_patterns=settings.exclude_patterns,
                        query_params=settings.query_params,
                        embedded_mode=embedded_mode,
                        max_urls=(
                            settings.max_urls
                            - discovered_count
                            - len(discovered_urls)
                        ),
                    )
                except (OSError, TypeError, ValueError, re.error) as exc:
                    result.errors.append(
                        f"{parent.canonical_url}: link discovery failed: {exc}"
                    )
                    continue
                for url in page_urls:
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    discovered_urls.append(url)
                    if discovered_count + len(discovered_urls) >= settings.max_urls:
                        break

            if not discovered_urls:
                break
            discovered_metadata = dict(task.metadata)
            discovered_metadata["discovery"] = {
                "method": "same_origin_links",
                "depth": depth,
                "max_depth": settings.max_depth,
            }
            discovered_metadata[_ALLOWED_FINAL_ORIGINS_KEY] = sorted(seed_origins)
            page_result = super().collect(
                replace(
                    task,
                    urls=tuple(discovered_urls),
                    metadata=discovered_metadata,
                )
            )
            self._merge_result(result, page_result)
            discovered_count += len(discovered_urls)
            frontier = [
                record
                for record in page_result.records
                if record.error is None
                and (record.http_status == 304 or record.payload_path)
            ]

    def collect(self, task: CollectorTask) -> CollectorResult:
        """Collect seeds, then bounded sitemap and same-origin link discoveries."""

        raw_link_settings = task.metadata.get("link_discovery")
        link_settings: LinkDiscoverySettings | None = None
        link_settings_error: str | None = None
        if raw_link_settings is not None:
            if not isinstance(raw_link_settings, Mapping):
                link_settings_error = "link_discovery must be a mapping"
            else:
                try:
                    link_settings = LinkDiscoverySettings.from_mapping(
                        raw_link_settings
                    )
                except (ValueError, re.error) as exc:
                    link_settings_error = str(exc)

        seed_task = task
        if link_settings is not None and link_settings.enabled:
            seed_origins = {
                origin
                for url in task.urls
                if (
                    identity := self._discovery_url_identity(
                        url,
                        link_settings,
                    )
                )
                and (origin := _http_origin(identity))
            }
            if seed_origins:
                seed_metadata = dict(task.metadata)
                seed_metadata[_ALLOWED_FINAL_ORIGINS_KEY] = sorted(seed_origins)
                seed_task = replace(task, metadata=seed_metadata)

        result = super().collect(seed_task)
        seed_records = list(result.records)
        if not task.enabled or not task.urls:
            return result
        raw_settings = task.metadata.get("sitemap_discovery")
        if raw_settings is not None:
            if not isinstance(raw_settings, Mapping):
                result.errors.append("sitemap_discovery must be a mapping")
            else:
                try:
                    settings = SitemapDiscoverySettings.from_mapping(raw_settings)
                except ValueError as exc:
                    result.errors.append(str(exc))
                else:
                    if settings.enabled:
                        discovery = self.sitemap_loader.discover(task, settings)
                        result.records.extend(discovery.records)
                        result.errors.extend(discovery.errors)
                        if discovery.urls:
                            discovered_metadata = dict(task.metadata)
                            discovered_metadata["discovery"] = {
                                "method": "sitemap",
                                "configured_sitemap_urls": list(settings.urls),
                            }
                            discovered_task = replace(
                                task,
                                urls=tuple(discovery.urls),
                                metadata=discovered_metadata,
                            )
                            self._merge_result(
                                result,
                                super().collect(discovered_task),
                            )

        if raw_link_settings is None:
            return result
        if link_settings_error is not None:
            result.errors.append(link_settings_error)
            return result
        if link_settings is None:
            return result
        self._collect_discovered_links(
            result,
            task,
            seed_records,
            link_settings,
        )
        return result
