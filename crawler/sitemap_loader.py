"""Bounded, configuration-driven sitemap discovery."""

from __future__ import annotations

import gzip
import io
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree

import requests

from .http_client import HttpClient, RobotsDeniedError, canonical_request_url
from .models import CollectorTask, RawRecord, SourceType
from .storage import RawWriter


_SITEMAP_ROLE = "discovery_sitemap"
_DEFAULT_MAX_URLS = 200
_DEFAULT_MAX_SITEMAPS = 20
MAX_CONFIGURED_URLS = 5_000
MAX_CONFIGURED_SITEMAPS = 100
MAX_SITEMAP_REDIRECTS = 5
MAX_SITEMAP_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_SITEMAP_DECOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_SITEMAP_LOCATIONS = 50_000
MAX_SITEMAP_XML_ELEMENTS = 200_000
MAX_SITEMAP_LOC_LENGTH = 8_192
MAX_PERCENT_DECODE_PASSES = 8
_SITEMAP_READ_CHUNK_BYTES = 64 * 1024

_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_UNSAFE_XML_DECLARATION = re.compile(
    br"<!\s*(?:DOCTYPE|ENTITY)\b",
    flags=re.IGNORECASE,
)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Sequence[object] = (value,)
    elif isinstance(value, Sequence):
        values = value
    else:
        raise ValueError(f"sitemap_discovery.{field_name} must be a string or list")
    return tuple(str(item).strip() for item in values if str(item).strip())


def _bounded_integer(
    value: object,
    field_name: str,
    default: int,
    hard_maximum: int,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"sitemap_discovery.{field_name} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"sitemap_discovery.{field_name} must be a non-negative integer"
        ) from exc
    if parsed < 0:
        raise ValueError(f"sitemap_discovery.{field_name} must be non-negative")
    if parsed > hard_maximum:
        raise ValueError(
            f"sitemap_discovery.{field_name} exceeds global maximum "
            f"{hard_maximum}"
        )
    return parsed


def _safe_decoded_path(path: str) -> str | None:
    """Decode bounded percent-encoding layers and reject traversal syntax."""

    candidate = path or "/"
    for pass_number in range(MAX_PERCENT_DECODE_PASSES + 1):
        if _INVALID_PERCENT_ESCAPE.search(candidate):
            return None
        if "\\" in candidate or any(ord(character) < 32 or ord(character) == 127 for character in candidate):
            return None
        if any(segment in {".", ".."} for segment in candidate.split("/")):
            return None
        try:
            decoded = unquote(candidate, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return None
        if decoded == candidate:
            return candidate
        if pass_number == MAX_PERCENT_DECODE_PASSES:
            return None
        candidate = decoded
    return None


def _path_prefixes(value: object, field_name: str) -> tuple[str, ...]:
    prefixes = _string_tuple(value, field_name)
    normalized: list[str] = []
    for raw_prefix in prefixes:
        candidate = (
            raw_prefix
            if raw_prefix.startswith("/")
            else f"/{raw_prefix}"
        )
        decoded = _safe_decoded_path(candidate)
        if decoded is None:
            raise ValueError(
                f"sitemap_discovery.{field_name} contains an unsafe path prefix"
            )
        normalized.append(
            "/" if decoded == "/" else decoded.rstrip("/")
        )
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class SitemapDiscoverySettings:
    """Validated sitemap discovery settings carried in source metadata."""

    enabled: bool = False
    urls: tuple[str, ...] = ()
    allowed_path_prefixes: tuple[str, ...] = ()
    excluded_path_prefixes: tuple[str, ...] = ()
    max_urls: int = _DEFAULT_MAX_URLS
    max_sitemaps: int = _DEFAULT_MAX_SITEMAPS

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "SitemapDiscoverySettings":
        enabled = value.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("sitemap_discovery.enabled must be a boolean")
        urls = _string_tuple(value.get("urls"), "urls")
        if enabled and not urls:
            raise ValueError("enabled sitemap_discovery requires at least one URL")
        return cls(
            enabled=enabled,
            urls=urls,
            allowed_path_prefixes=_path_prefixes(
                value.get("allowed_path_prefixes"),
                "allowed_path_prefixes",
            ),
            excluded_path_prefixes=_path_prefixes(
                value.get("excluded_path_prefixes"),
                "excluded_path_prefixes",
            ),
            max_urls=_bounded_integer(
                value.get("max_urls"),
                "max_urls",
                _DEFAULT_MAX_URLS,
                MAX_CONFIGURED_URLS,
            ),
            max_sitemaps=_bounded_integer(
                value.get("max_sitemaps"),
                "max_sitemaps",
                _DEFAULT_MAX_SITEMAPS,
                MAX_CONFIGURED_SITEMAPS,
            ),
        )


@dataclass(slots=True)
class SitemapDiscoveryResult:
    """Discovered page URLs plus auditable sitemap acquisition records."""

    urls: list[str] = field(default_factory=list)
    records: list[RawRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ParsedSitemap:
    kind: str
    locations: tuple[str, ...]
    was_gzip: bool
    decompressed_size: int


def normalize_discovered_url(value: object, *, base_url: str) -> str | None:
    """Resolve and canonicalize an HTTP(S) URL while removing its fragment."""

    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        raw_parts = urlsplit(raw)
        if _safe_decoded_path(raw_parts.path or "/") is None:
            return None
        resolved = urljoin(base_url, raw)
        parts = urlsplit(resolved)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    if _safe_decoded_path(parts.path or "/") is None:
        return None
    try:
        return canonical_request_url(resolved)
    except ValueError:
        return None


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(canonical_request_url(url))
    return parts.scheme, parts.netloc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _path_has_prefix(path: str, prefix: str) -> bool:
    if prefix == "/":
        return path.startswith("/")
    return path == prefix or path.startswith(f"{prefix}/")


def parse_sitemap(payload: bytes, *, source_url: str) -> ParsedSitemap:
    """Parse a sitemap index or URL set, including gzip-compressed payloads."""

    if len(payload) > MAX_SITEMAP_RESPONSE_BYTES:
        raise ValueError(
            "sitemap response exceeds hard byte limit "
            f"{MAX_SITEMAP_RESPONSE_BYTES}"
        )
    was_gzip = payload.startswith(b"\x1f\x8b")
    if was_gzip:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
                body = stream.read(MAX_SITEMAP_DECOMPRESSED_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise ValueError(f"invalid gzip sitemap: {exc}") from exc
    else:
        body = payload
    if len(body) > MAX_SITEMAP_DECOMPRESSED_BYTES:
        raise ValueError(
            "decompressed sitemap exceeds hard byte limit "
            f"{MAX_SITEMAP_DECOMPRESSED_BYTES}"
        )
    if _UNSAFE_XML_DECLARATION.search(body):
        raise ValueError("sitemap XML declarations may not contain DTD or entities")

    root_name: str | None = None
    container_name: str | None = None
    kind: str | None = None
    element_count = 0
    location_count = 0
    element_stack: list[str] = []
    locations: list[str] = []
    try:
        events = ElementTree.iterparse(
            io.BytesIO(body),
            events=("start", "end"),
        )
        for event, element in events:
            name = _local_name(element.tag)
            if event == "start":
                element_count += 1
                if element_count > MAX_SITEMAP_XML_ELEMENTS:
                    raise ValueError(
                        "sitemap XML exceeds hard element limit "
                        f"{MAX_SITEMAP_XML_ELEMENTS}"
                    )
                element_stack.append(name)
                if root_name is None:
                    root_name = name
                    if root_name == "sitemapindex":
                        container_name = "sitemap"
                        kind = "sitemapindex"
                    elif root_name == "urlset":
                        container_name = "url"
                        kind = "urlset"
                    else:
                        raise ValueError(
                            "unsupported sitemap root element: "
                            f"{root_name or 'unknown'}"
                        )
                continue

            if (
                name == "loc"
                and len(element_stack) >= 2
                and element_stack[-2] == container_name
            ):
                location_count += 1
                if location_count > MAX_SITEMAP_LOCATIONS:
                    raise ValueError(
                        "sitemap exceeds hard loc limit "
                        f"{MAX_SITEMAP_LOCATIONS}"
                    )
                location = "".join(element.itertext()).strip()
                if len(location) > MAX_SITEMAP_LOC_LENGTH:
                    raise ValueError(
                        "sitemap loc exceeds hard character limit "
                        f"{MAX_SITEMAP_LOC_LENGTH}"
                    )
                normalized = normalize_discovered_url(
                    location,
                    base_url=source_url,
                )
                if normalized:
                    locations.append(normalized)
            if element_stack:
                element_stack.pop()
            element.clear()
    except ElementTree.ParseError as exc:
        raise ValueError(f"invalid sitemap XML: {exc}") from exc

    if kind is None:
        raise ValueError("sitemap XML has no root element")
    return ParsedSitemap(
        kind=kind,
        locations=tuple(locations),
        was_gzip=was_gzip,
        decompressed_size=len(body),
    )


class SitemapLoader:
    """Fetch nested sitemaps through the shared HTTP client under strict caps."""

    def __init__(
        self,
        client: HttpClient,
        writer: RawWriter,
        source_type: SourceType | str,
    ) -> None:
        self.client = client
        self.writer = writer
        self.source_type = SourceType.parse(source_type)

    @staticmethod
    def _page_allowed(
        url: str,
        *,
        seed_origins: set[tuple[str, str]],
        settings: SitemapDiscoverySettings,
    ) -> bool:
        if _origin(url) not in seed_origins:
            return False
        path = _safe_decoded_path(urlsplit(url).path or "/")
        if path is None:
            return False
        if settings.allowed_path_prefixes and not any(
            _path_has_prefix(path, prefix)
            for prefix in settings.allowed_path_prefixes
        ):
            return False
        if any(
            _path_has_prefix(path, prefix)
            for prefix in settings.excluded_path_prefixes
        ):
            return False
        return True

    @staticmethod
    def _metadata(
        task: CollectorTask,
        *,
        sitemap_url: str,
        depth: int,
        **extra: object,
    ) -> dict[str, object]:
        return {
            **task.metadata,
            "record_role": _SITEMAP_ROLE,
            "discovery": {
                "method": "sitemap",
                "sitemap_url": sitemap_url,
                "depth": depth,
            },
            **extra,
        }

    def _write_failed_response(
        self,
        task: CollectorTask,
        sitemap_url: str,
        response: requests.Response,
        error: str,
        metadata: Mapping[str, object],
        *,
        preserve_payload: bool = True,
    ) -> RawRecord:
        return self.writer.write_bytes(
            competitor=task.competitor,
            source_type=self.source_type,
            requested_url=sitemap_url,
            canonical_url=normalize_discovered_url(
                response.url or sitemap_url,
                base_url=sitemap_url,
            )
            or sitemap_url,
            final_url=response.url,
            payload=(
                response.content
                if preserve_payload and response.status_code != 304
                else None
            ),
            http_status=response.status_code,
            content_type=response.headers.get("Content-Type"),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            error=error,
            source_metadata=metadata,
        )

    def _fetch_same_origin(
        self,
        task: CollectorTask,
        sitemap_url: str,
        seed_origins: set[tuple[str, str]],
    ) -> tuple[requests.Response, str, int, str | None]:
        """Follow only validated same-origin redirects through ``HttpClient``."""

        current_url = sitemap_url
        redirect_count = 0
        while True:
            response = self.client.get(
                current_url,
                conditional=False,
                force=task.force,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code not in _REDIRECT_STATUSES:
                return response, current_url, redirect_count, None

            location = response.headers.get("Location")
            target = normalize_discovered_url(
                location,
                base_url=current_url,
            )
            if target is None:
                response.close()
                return (
                    response,
                    current_url,
                    redirect_count,
                    "sitemap redirect target is missing or unsafe",
                )
            if _origin(target) not in seed_origins:
                response.close()
                return (
                    response,
                    current_url,
                    redirect_count,
                    "sitemap redirected outside configured seed origins",
                )
            if redirect_count >= MAX_SITEMAP_REDIRECTS:
                response.close()
                return (
                    response,
                    current_url,
                    redirect_count,
                    "sitemap exceeds hard redirect limit "
                    f"{MAX_SITEMAP_REDIRECTS}",
                )
            response.close()
            current_url = target
            redirect_count += 1

    @staticmethod
    def _read_bounded_response(
        response: requests.Response,
    ) -> tuple[bytes | None, int]:
        """Read a streamed sitemap response without exceeding the byte cap."""

        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = -1
            if declared_length > MAX_SITEMAP_RESPONSE_BYTES:
                response.close()
                return None, declared_length

        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(
                chunk_size=_SITEMAP_READ_CHUNK_BYTES,
            ):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_SITEMAP_RESPONSE_BYTES:
                    response.close()
                    return None, total
                chunks.append(chunk)
        finally:
            response.close()
        payload = b"".join(chunks)
        response._content = payload
        response._content_consumed = True
        return payload, total

    def discover(
        self,
        task: CollectorTask,
        settings: SitemapDiscoverySettings,
    ) -> SitemapDiscoveryResult:
        result = SitemapDiscoveryResult()
        if not settings.enabled or settings.max_sitemaps == 0:
            return result

        normalized_seeds = [
            normalized
            for seed in task.urls
            if (normalized := normalize_discovered_url(seed, base_url=seed))
        ]
        if not normalized_seeds:
            result.errors.append(
                f"{task.competitor}/{self.source_type.value}: "
                "sitemap discovery requires an HTTP(S) seed URL"
            )
            return result
        seed_origins = {_origin(seed) for seed in normalized_seeds}
        seed_urls = set(normalized_seeds)
        base_url = normalized_seeds[0]

        queue: deque[tuple[str, int]] = deque()
        queued_sitemaps: set[str] = set()
        for configured_url in settings.urls:
            sitemap_url = normalize_discovered_url(configured_url, base_url=base_url)
            if sitemap_url is None:
                result.errors.append(f"invalid sitemap URL: {configured_url}")
                continue
            if _origin(sitemap_url) not in seed_origins:
                result.errors.append(
                    f"sitemap URL is outside configured seed origins: {sitemap_url}"
                )
                continue
            if sitemap_url not in queued_sitemaps:
                queue.append((sitemap_url, 0))
                queued_sitemaps.add(sitemap_url)

        attempted_sitemaps = 0
        discovered_set: set[str] = set()
        while (
            queue
            and attempted_sitemaps < settings.max_sitemaps
            and len(result.urls) < settings.max_urls
        ):
            sitemap_url, depth = queue.popleft()
            attempted_sitemaps += 1
            base_metadata = self._metadata(
                task,
                sitemap_url=sitemap_url,
                depth=depth,
            )
            try:
                (
                    response,
                    final_request_url,
                    redirect_count,
                    redirect_error,
                ) = self._fetch_same_origin(
                    task,
                    sitemap_url,
                    seed_origins,
                )
            except (requests.RequestException, RobotsDeniedError) as exc:
                message = str(exc)
                result.errors.append(f"{sitemap_url}: {message}")
                result.records.append(
                    self.writer.write_error(
                        competitor=task.competitor,
                        source_type=self.source_type,
                        requested_url=sitemap_url,
                        error=message,
                        source_metadata=base_metadata,
                    )
                )
                continue

            if redirect_error:
                metadata = {
                    **base_metadata,
                    "redirect_target": response.headers.get("Location"),
                    "redirect_rejected": True,
                    "redirect_count": redirect_count,
                }
                result.errors.append(f"{sitemap_url}: {redirect_error}")
                result.records.append(
                    self._write_failed_response(
                        task,
                        sitemap_url,
                        response,
                        redirect_error,
                        metadata,
                        preserve_payload=False,
                    )
                )
                continue

            try:
                response_payload, response_size = self._read_bounded_response(
                    response
                )
            except requests.RequestException as exc:
                message = f"sitemap response read failed: {exc}"
                result.errors.append(f"{sitemap_url}: {message}")
                result.records.append(
                    self.writer.write_error(
                        competitor=task.competitor,
                        source_type=self.source_type,
                        requested_url=sitemap_url,
                        error=message,
                        final_url=response.url,
                        http_status=response.status_code,
                        source_metadata=base_metadata,
                    )
                )
                continue
            if response_payload is None:
                message = (
                    "sitemap response exceeds hard byte limit "
                    f"{MAX_SITEMAP_RESPONSE_BYTES}"
                )
                metadata = {
                    **base_metadata,
                    "response_size": response_size,
                    "resource_limit_rejected": True,
                    "redirect_count": redirect_count,
                }
                result.errors.append(f"{sitemap_url}: {message}")
                result.records.append(
                    self._write_failed_response(
                        task,
                        sitemap_url,
                        response,
                        message,
                        metadata,
                        preserve_payload=False,
                    )
                )
                continue

            if not 200 <= response.status_code < 300:
                message = f"HTTP {response.status_code}"
                result.errors.append(f"{sitemap_url}: {message}")
                result.records.append(
                    self._write_failed_response(
                        task,
                        sitemap_url,
                        response,
                        message,
                        base_metadata,
                    )
                )
                continue

            final_sitemap_url = normalize_discovered_url(
                response.url or final_request_url,
                base_url=final_request_url,
            )
            if (
                final_sitemap_url is None
                or _origin(final_sitemap_url) not in seed_origins
            ):
                message = "sitemap redirected outside configured seed origins"
                metadata = {
                    **base_metadata,
                    "redirect_target": response.url,
                    "redirect_rejected": True,
                }
                result.errors.append(f"{sitemap_url}: {message}")
                result.records.append(
                    self._write_failed_response(
                        task,
                        sitemap_url,
                        response,
                        message,
                        metadata,
                    )
                )
                continue

            try:
                parsed = parse_sitemap(
                    response.content,
                    source_url=final_sitemap_url,
                )
            except ValueError as exc:
                message = str(exc)
                metadata = {**base_metadata, "parse_error": message}
                result.errors.append(f"{sitemap_url}: {message}")
                result.records.append(
                    self._write_failed_response(
                        task,
                        sitemap_url,
                        response,
                        message,
                        metadata,
                    )
                )
                continue

            metadata = {
                **base_metadata,
                "sitemap_kind": parsed.kind,
                "sitemap_location_count": len(parsed.locations),
                "sitemap_gzip": parsed.was_gzip,
                "sitemap_response_size": response_size,
                "sitemap_decompressed_size": parsed.decompressed_size,
                "redirect_count": redirect_count,
            }
            result.records.append(
                self.writer.write_response(
                    competitor=task.competitor,
                    source_type=self.source_type,
                    requested_url=sitemap_url,
                    canonical_url=final_sitemap_url,
                    response=response,
                    source_metadata=metadata,
                )
            )

            if parsed.kind == "sitemapindex":
                for nested_url in parsed.locations:
                    if _origin(nested_url) not in seed_origins:
                        continue
                    if nested_url not in queued_sitemaps:
                        queue.append((nested_url, depth + 1))
                        queued_sitemaps.add(nested_url)
                continue

            for page_url in parsed.locations:
                if len(result.urls) >= settings.max_urls:
                    break
                if page_url in seed_urls or page_url in discovered_set:
                    continue
                if not self._page_allowed(
                    page_url,
                    seed_origins=seed_origins,
                    settings=settings,
                ):
                    continue
                discovered_set.add(page_url)
                result.urls.append(page_url)

        return result


__all__ = [
    "MAX_CONFIGURED_SITEMAPS",
    "MAX_CONFIGURED_URLS",
    "MAX_PERCENT_DECODE_PASSES",
    "MAX_SITEMAP_DECOMPRESSED_BYTES",
    "MAX_SITEMAP_LOCATIONS",
    "MAX_SITEMAP_LOC_LENGTH",
    "MAX_SITEMAP_REDIRECTS",
    "MAX_SITEMAP_RESPONSE_BYTES",
    "MAX_SITEMAP_XML_ELEMENTS",
    "ParsedSitemap",
    "SitemapDiscoveryResult",
    "SitemapDiscoverySettings",
    "SitemapLoader",
    "normalize_discovered_url",
    "parse_sitemap",
]
