"""Pure, bounded discovery of same-origin page links from HTML."""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Pattern, TypeAlias
from urllib.parse import (
    parse_qsl,
    unquote,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

from bs4 import BeautifulSoup


RegexLike: TypeAlias = str | Pattern[str]
QueryParams: TypeAlias = (
    Mapping[str, object] | Sequence[tuple[str, object]] | None
)

_TRAE_DOCUMENT_ROOTS = frozenset({"ide", "solo", "plugin", "enterprise"})
_ROUTER_ASSIGNMENT = re.compile(
    r"(?:window\s*\.\s*_ROUTER_DATA|"
    r"window\s*\[\s*['\"]_ROUTER_DATA['\"]\s*\])\s*="
)
_TRACKING_QUERY_NAMES = frozenset(
    {
        "_hsenc",
        "_hsmi",
        "dclid",
        "fbclid",
        "gbraid",
        "gclid",
        "hsctatracking",
        "igshid",
        "mc_cid",
        "mc_eid",
        "mkt_tok",
        "msclkid",
        "oly_anon_id",
        "oly_enc_id",
        "rb_clickid",
        "s_cid",
        "twclid",
        "vero_conv",
        "vero_id",
        "wbraid",
        "yclid",
    }
)


def _config_strings(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Sequence[object] = (value,)
    elif isinstance(value, Sequence) and not isinstance(
        value, (bytes, bytearray)
    ):
        values = value
    else:
        raise ValueError(f"link_discovery.{field_name} must be text or a list")
    return tuple(str(item).strip() for item in values if str(item).strip())


def _config_integer(
    value: object,
    field_name: str,
    *,
    default: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"link_discovery.{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"link_discovery.{field_name} must be an integer"
        ) from exc
    if not 0 <= parsed <= maximum:
        raise ValueError(
            f"link_discovery.{field_name} must be between 0 and {maximum}"
        )
    return parsed


@dataclass(frozen=True, slots=True)
class LinkDiscoverySettings:
    """Validated limits and filters for recursive same-origin page discovery."""

    enabled: bool = False
    allowed_path_prefixes: tuple[str, ...] = ()
    excluded_path_prefixes: tuple[str, ...] = ()
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    query_params: QueryParams = None
    embedded_mode: str | None = None
    max_depth: int = 1
    max_urls: int = 200

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "LinkDiscoverySettings":
        enabled = value.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("link_discovery.enabled must be a boolean")
        allowed = _config_strings(
            value.get("allowed_path_prefixes"),
            "allowed_path_prefixes",
        )
        if enabled and not allowed:
            raise ValueError(
                "enabled link_discovery requires allowed_path_prefixes"
            )
        query_params = value.get("query_params")
        _configured_query_pairs(query_params)  # Validate without mutating it.
        embedded_mode = str(value.get("embedded_mode") or "").strip() or None
        if embedded_mode not in {None, "trae_router_document"}:
            raise ValueError(
                f"unsupported link_discovery.embedded_mode: {embedded_mode}"
            )
        include_patterns = _config_strings(
            value.get("include_patterns"),
            "include_patterns",
        )
        exclude_patterns = _config_strings(
            value.get("exclude_patterns"),
            "exclude_patterns",
        )
        _compile_patterns(include_patterns)
        _compile_patterns(exclude_patterns)
        settings = cls(
            enabled=enabled,
            allowed_path_prefixes=allowed,
            excluded_path_prefixes=_config_strings(
                value.get("excluded_path_prefixes"),
                "excluded_path_prefixes",
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            query_params=query_params,  # type: ignore[arg-type]
            embedded_mode=embedded_mode,
            max_depth=_config_integer(
                value.get("max_depth"),
                "max_depth",
                default=1,
                maximum=5,
            ),
            max_urls=_config_integer(
                value.get("max_urls"),
                "max_urls",
                default=200,
                maximum=1_000,
            ),
        )
        _normalized_prefixes(settings.allowed_path_prefixes)
        _normalized_prefixes(settings.excluded_path_prefixes)
        return settings


def _contains_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _has_path_traversal(path: str) -> bool:
    """Detect literal and repeatedly percent-encoded dot segments."""

    decoded = path
    for _ in range(4):
        normalized = decoded.replace("\\", "/")
        if any(segment in {".", ".."} for segment in normalized.split("/")):
            return True
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    normalized = decoded.replace("\\", "/")
    return any(segment in {".", ".."} for segment in normalized.split("/"))


def _canonical_host(parts: object) -> tuple[str, int | None] | None:
    try:
        hostname = getattr(parts, "hostname")
        port = getattr(parts, "port")
    except ValueError:
        return None
    if not hostname:
        return None
    hostname = str(hostname).rstrip(".").lower()
    if not hostname or "%" in hostname or _contains_control_characters(hostname):
        return None
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return hostname, port


def _origin(parts: object) -> tuple[str, str, int] | None:
    scheme = str(getattr(parts, "scheme", "")).lower()
    if scheme not in {"http", "https"}:
        return None
    host_and_port = _canonical_host(parts)
    if host_and_port is None:
        return None
    hostname, port = host_and_port
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    return scheme, hostname, effective_port


def _netloc(parts: object, scheme: str) -> str | None:
    host_and_port = _canonical_host(parts)
    if host_and_port is None:
        return None
    hostname, port = host_and_port
    formatted_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is None or (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    ):
        return formatted_host
    return f"{formatted_host}:{port}"


def _is_tracking_query_name(name: str) -> bool:
    lowered = name.casefold()
    return lowered.startswith("utm_") or lowered in _TRACKING_QUERY_NAMES


def _expand_query_value(value: object) -> Iterable[str]:
    if isinstance(value, (list, tuple)):
        for item in value:
            yield "" if item is None else str(item)
        return
    if isinstance(value, (set, frozenset)):
        for item in sorted(value, key=str):
            yield "" if item is None else str(item)
        return
    yield "" if value is None else str(value)


def _configured_query_pairs(query_params: QueryParams) -> list[tuple[str, str]]:
    if query_params is None:
        return []
    if isinstance(query_params, Mapping):
        items: Iterable[tuple[object, object]] = query_params.items()
    elif isinstance(query_params, Sequence) and not isinstance(
        query_params, (str, bytes, bytearray)
    ):
        items = query_params
    else:
        raise TypeError("query_params must be a mapping or a sequence of pairs")

    pairs: list[tuple[str, str]] = []
    for raw_key, value in items:
        key = str(raw_key)
        if not key or _contains_control_characters(key):
            raise ValueError("query parameter names must be non-empty text")
        if _is_tracking_query_name(key):
            continue
        pairs.extend((key, item) for item in _expand_query_value(value))
    return pairs


def _compile_patterns(patterns: Sequence[RegexLike]) -> tuple[Pattern[str], ...]:
    return tuple(
        re.compile(pattern) if isinstance(pattern, str) else pattern
        for pattern in patterns
    )


def _normalized_prefixes(prefixes: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in prefixes:
        prefix = str(value).strip()
        if not prefix:
            continue
        if _contains_control_characters(prefix) or _has_path_traversal(prefix):
            raise ValueError(f"unsafe path prefix: {value!r}")
        prefix = unquote(prefix).replace("\\", "/")
        if not prefix.startswith("/"):
            prefix = f"/{prefix}"
        normalized.append(prefix if prefix == "/" else prefix.rstrip("/"))
    return tuple(normalized)


def _path_has_prefix(path: str, prefix: str) -> bool:
    if prefix == "/":
        return True
    return path == prefix or path.startswith(f"{prefix}/")


def _matches_patterns(
    url: str,
    *,
    include_patterns: tuple[Pattern[str], ...],
    exclude_patterns: tuple[Pattern[str], ...],
) -> bool:
    parts = urlsplit(url)
    path_and_query = parts.path + (f"?{parts.query}" if parts.query else "")
    targets = (url, path_and_query)
    if include_patterns and not any(
        pattern.search(target)
        for pattern in include_patterns
        for target in targets
    ):
        return False
    return not any(
        pattern.search(target)
        for pattern in exclude_patterns
        for target in targets
    )


def _normalize_candidate(
    href: object,
    *,
    page_url: str,
    page_origin: tuple[str, str, int],
    configured_query_pairs: Sequence[tuple[str, str]],
) -> str | None:
    raw = str(href or "").strip()
    if not raw or _contains_control_characters(raw):
        return None
    try:
        raw_parts = urlsplit(raw)
    except ValueError:
        return None
    if raw_parts.username is not None or raw_parts.password is not None:
        return None
    if _has_path_traversal(raw_parts.path):
        return None

    try:
        resolved = urljoin(page_url, raw)
        parts = urlsplit(resolved)
    except ValueError:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    candidate_origin = _origin(parts)
    if candidate_origin is None or candidate_origin != page_origin:
        return None
    if _has_path_traversal(parts.path):
        return None

    scheme = parts.scheme.lower()
    netloc = _netloc(parts, scheme)
    if netloc is None:
        return None
    path = parts.path or "/"

    existing_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_query_name(key)
    ]
    overridden_names = {key for key, _ in configured_query_pairs}
    query_pairs = [
        pair for pair in existing_pairs if pair[0] not in overridden_names
    ]
    query_pairs.extend(configured_query_pairs)
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def _router_payloads(html: str) -> Iterable[Mapping[str, object]]:
    decoder = json.JSONDecoder()
    for assignment in _ROUTER_ASSIGNMENT.finditer(html):
        remainder = html[assignment.end() :].lstrip()
        try:
            payload, _ = decoder.raw_decode(remainder)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping):
            yield payload


def _router_containers(payload: Mapping[str, object]) -> Iterable[Mapping[str, object]]:
    loader_data = payload.get("loaderData")
    if not isinstance(loader_data, Mapping):
        return
    for key in ("layout", "$"):
        container = loader_data.get(key)
        if isinstance(container, Mapping):
            yield container


def _bus_documents(bus_structure: object) -> Iterable[Mapping[str, object]]:
    if not isinstance(bus_structure, (list, tuple, Mapping)):
        return
    queue: deque[object] = deque(
        [bus_structure] if isinstance(bus_structure, Mapping) else bus_structure
    )
    while queue:
        item = queue.popleft()
        if not isinstance(item, Mapping):
            continue
        yield item
        for key in ("children", "items", "nodes", "subs"):
            children = item.get(key)
            if isinstance(children, Mapping):
                queue.append(children)
            elif isinstance(children, (list, tuple)):
                queue.extend(children)


def _is_directory(item: Mapping[str, object]) -> bool:
    value = item.get("is_dir")
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes"}
    return value is True or value == 1


def _trae_router_urls(html: str, page_url: str) -> Iterable[str]:
    for payload in _router_payloads(html):
        for container in _router_containers(payload):
            page_detail = container.get("pageDetail")
            if not isinstance(page_detail, Mapping):
                continue
            root = str(page_detail.get("path") or "").strip().strip("/").lower()
            if root not in _TRAE_DOCUMENT_ROOTS:
                continue
            for item in _bus_documents(container.get("busStructure")):
                if _is_directory(item):
                    continue
                document_path = str(item.get("path") or "").strip().strip("/")
                if not document_path:
                    continue
                if document_path == root or document_path.startswith(f"{root}/"):
                    relative_path = document_path
                else:
                    relative_path = f"{root}/{document_path}"
                yield urljoin(page_url, f"/{relative_path}")


def discover_page_urls(
    html: str | bytes,
    *,
    page_url: str,
    allowed_path_prefixes: Sequence[str] = (),
    excluded_path_prefixes: Sequence[str] = (),
    include_patterns: Sequence[RegexLike] = (),
    exclude_patterns: Sequence[RegexLike] = (),
    query_params: QueryParams = None,
    embedded_mode: str | None = None,
    max_urls: int = 200,
) -> list[str]:
    """Return controlled, same-origin links without performing network requests.

    Regex constraints are searched against both the complete canonical URL and its
    path-plus-query form. Configured query parameters override same-named values
    found in links; fragments and common advertising trackers are removed.
    """

    if isinstance(max_urls, bool) or not isinstance(max_urls, int) or max_urls < 0:
        raise ValueError("max_urls must be a non-negative integer")
    if embedded_mode not in {None, "", "trae_router_document"}:
        raise ValueError(f"unsupported embedded_mode: {embedded_mode}")
    if max_urls == 0:
        return []

    source = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    if not isinstance(source, str):
        raise TypeError("html must be text or bytes")

    try:
        page_parts = urlsplit(page_url)
    except ValueError:
        return []
    if page_parts.username is not None or page_parts.password is not None:
        return []
    if _has_path_traversal(page_parts.path):
        return []
    page_origin = _origin(page_parts)
    if page_origin is None:
        return []

    configured_query_pairs = _configured_query_pairs(query_params)
    allowed_prefixes = _normalized_prefixes(allowed_path_prefixes)
    excluded_prefixes = _normalized_prefixes(excluded_path_prefixes)
    compiled_includes = _compile_patterns(include_patterns)
    compiled_excludes = _compile_patterns(exclude_patterns)

    soup = BeautifulSoup(source, "html.parser")
    candidates: list[object] = [
        anchor.get("href") for anchor in soup.find_all("a", href=True)
    ]
    if embedded_mode == "trae_router_document":
        candidates.extend(_trae_router_urls(source, page_url))

    discovered: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_candidate(
            candidate,
            page_url=page_url,
            page_origin=page_origin,
            configured_query_pairs=configured_query_pairs,
        )
        if normalized is None or normalized in seen:
            continue
        decoded_path = unquote(urlsplit(normalized).path).replace("\\", "/")
        if allowed_prefixes and not any(
            _path_has_prefix(decoded_path, prefix) for prefix in allowed_prefixes
        ):
            continue
        if any(
            _path_has_prefix(decoded_path, prefix) for prefix in excluded_prefixes
        ):
            continue
        if not _matches_patterns(
            normalized,
            include_patterns=compiled_includes,
            exclude_patterns=compiled_excludes,
        ):
            continue
        seen.add(normalized)
        discovered.append(normalized)
        if len(discovered) >= max_urls:
            break
    return discovered


__all__ = [
    "LinkDiscoverySettings",
    "QueryParams",
    "RegexLike",
    "discover_page_urls",
]
