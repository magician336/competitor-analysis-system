"""HTML, RSS and Atom changelog collector."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag
from dateutil import parser as date_parser

from .base import PageCollector, visible_text_length
from .http_client import RobotsDeniedError
from .models import CollectorResult, CollectorTask, SourceType, isoformat_utc
from .rss_loader import RSSLoader


LOGGER = logging.getLogger(__name__)

_MONTHS = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?"
)
_DATE_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T\s]+\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:\s*(?:Z|[+-]\d{2}:?\d{2}))?)?\b",
        rf"\b(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?\s+20\d{{2}}\b",
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})[,]?\s+20\d{{2}}\b",
        r"\b20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?\b",
    )
)
_NEXT_TEXT = {
    "next",
    "next page",
    "older",
    "older posts",
    "下一页",
    "下页",
    "后一页",
    "后页",
    "›",
    "»",
    "→",
}
_ENTRY_CLASS_HINT = re.compile(
    r"(?:^|[-_\s])(entry|item|card|post|article|release|update|changelog)(?:$|[-_\s])",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _HtmlEntryLink:
    url: str
    title: str
    published_at: datetime


@dataclass(frozen=True, slots=True)
class _UndatedHtmlEntryLink:
    """An explicitly configured directory link whose date lives on the target page."""

    url: str
    title: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_date(value: object) -> datetime | None:
    """Parse a date only when the input contains an explicit four-digit year."""

    if value is None:
        return None
    text = " ".join(str(value).split())
    match = next((pattern.search(text) for pattern in _DATE_PATTERNS if pattern.search(text)), None)
    if match is None:
        return None
    candidate = match.group(0)
    candidate = re.sub(
        r"(\d{1,2})(?:st|nd|rd|th)", r"\1", candidate, flags=re.IGNORECASE
    )
    candidate = (
        candidate.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
    )
    try:
        return _utc(date_parser.parse(candidate, fuzzy=False))
    except (ValueError, TypeError, OverflowError):
        return None


def _normalized_url(base_url: str, href: object) -> str | None:
    raw = str(href or "").strip()
    if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    absolute = urljoin(base_url, raw)
    parts = urlsplit(absolute)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            parts.query,
            "",
        )
    )


def _same_origin(base_url: str, candidate_url: str) -> bool:
    base = urlsplit(base_url)
    candidate = urlsplit(candidate_url)
    return (
        base.scheme.lower(),
        base.netloc.lower(),
    ) == (
        candidate.scheme.lower(),
        candidate.netloc.lower(),
    )


def _path_matches_prefixes(candidate_url: str, path_prefixes: tuple[str, ...]) -> bool:
    if not path_prefixes:
        return True
    raw_path = urlsplit(candidate_url).path or "/"
    decoded_path = unquote(raw_path)
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        return False
    for prefix in path_prefixes:
        normalized_prefix = "/" + prefix.strip().strip("/") if prefix.strip("/") else "/"
        if decoded_path == normalized_prefix or decoded_path.startswith(
            normalized_prefix.rstrip("/") + "/"
        ):
            return True
    return False


def _tag_marker(tag: Tag) -> str:
    values: list[str] = []
    for key in ("class", "id"):
        value = tag.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return " ".join(values)


def _is_bounded_entry_container(tag: Tag) -> bool:
    if tag.name in {"article", "li", "tr"}:
        return True
    if _ENTRY_CLASS_HINT.search(_tag_marker(tag)):
        return True
    if tag.name in {"div", "section"}:
        text_length = len(tag.get_text(" ", strip=True))
        return text_length <= 1_200 and len(tag.find_all("a", href=True)) <= 6
    return False


def _dates_in_tag(tag: Tag, *, include_text: bool) -> Iterable[datetime]:
    for key in (
        "datetime",
        "content",
        "data-date",
        "data-published",
        "data-published-at",
        "data-publish-date",
    ):
        parsed = _parse_date(tag.get(key))
        if parsed is not None:
            yield parsed

    for date_tag in tag.find_all(["time", "date"], limit=4):
        for candidate in (
            date_tag.get("datetime"),
            date_tag.get("content"),
            date_tag.get_text(" ", strip=True),
        ):
            parsed = _parse_date(candidate)
            if parsed is not None:
                yield parsed
                break

    if include_text:
        parsed = _parse_date(tag.get_text(" ", strip=True)[:1_500])
        if parsed is not None:
            yield parsed


def _entry_context(anchor: Tag) -> tuple[Tag | None, datetime | None]:
    parsed = next(iter(_dates_in_tag(anchor, include_text=True)), None)
    if parsed is not None:
        return anchor, parsed

    for parent in anchor.parents:
        if not isinstance(parent, Tag) or parent.name in {"body", "main", "html"}:
            break
        if not _is_bounded_entry_container(parent):
            continue
        parsed = next(iter(_dates_in_tag(parent, include_text=True)), None)
        if parsed is not None:
            return parent, parsed
    return None, None


def _is_primary_entry_anchor(anchor: Tag, container: Tag | None) -> bool:
    """Avoid treating auxiliary links inside a dated card as separate entries."""

    if container is None or container is anchor:
        return True
    heading = container.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    if heading:
        heading_link = heading.find("a", href=True)
        if heading_link is not None:
            return heading_link is anchor
    for candidate in container.find_all("a", href=True):
        if not _is_next_anchor(candidate):
            return candidate is anchor
    return False


def _link_title(anchor: Tag, container: Tag | None) -> str:
    title = " ".join(anchor.get_text(" ", strip=True).split())
    if title.casefold() in {"read more", "learn more", "查看详情", "详情"} and container:
        heading = container.find(["h1", "h2", "h3", "h4", "h5", "h6"])
        if heading:
            title = " ".join(heading.get_text(" ", strip=True).split())
    return title or "Untitled changelog entry"


def _is_next_anchor(anchor: Tag) -> bool:
    rel = anchor.get("rel") or []
    if isinstance(rel, str):
        rel = rel.split()
    if any(str(value).casefold() == "next" for value in rel):
        return True
    labels = (
        anchor.get_text(" ", strip=True),
        anchor.get("aria-label"),
        anchor.get("title"),
        *anchor.stripped_strings,
    )

    def normalise_label(label: object) -> str:
        value = " ".join(str(label).casefold().split())
        return re.sub(r"^[\s←→‹›«»]+|[\s←→‹›«»]+$", "", value)

    return any(
        normalise_label(label) in _NEXT_TEXT
        for label in labels
        if label
    )


def _extract_entry_links(soup: BeautifulSoup, page_url: str) -> list[_HtmlEntryLink]:
    output: list[_HtmlEntryLink] = []
    seen: set[str] = set()
    normalized_page = _normalized_url(page_url, page_url)
    for anchor in soup.find_all("a", href=True):
        if _is_next_anchor(anchor):
            continue
        url = _normalized_url(page_url, anchor.get("href"))
        if url is None or url == normalized_page or url in seen:
            continue
        container, published_at = _entry_context(anchor)
        if published_at is None:
            published_at = _parse_date(url)
        if published_at is None:
            continue
        if not _is_primary_entry_anchor(anchor, container):
            continue
        seen.add(url)
        output.append(
            _HtmlEntryLink(
                url=url,
                title=_link_title(anchor, container),
                published_at=published_at,
            )
        )
    return output


def _metadata_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Iterable):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _extract_configured_undated_links(
    soup: BeautifulSoup,
    page_url: str,
    *,
    selector: str | None,
    path_prefixes: tuple[str, ...] = (),
    text_pattern: str | None = None,
) -> list[_UndatedHtmlEntryLink]:
    """Return same-origin undated links from an explicitly configured directory.

    This path is intentionally opt-in.  A generic scan of every undated link
    would mistake site navigation and related-content links for changelog
    entries.  The CSS selector identifies the source-owned directory, while
    path prefixes and an optional text pattern provide two additional bounds.
    """

    if not selector:
        return []
    try:
        selected = soup.select(selector)
    except Exception as exc:
        LOGGER.warning("Invalid undated changelog selector %r: %s", selector, exc)
        return []
    try:
        title_pattern = re.compile(text_pattern, flags=re.IGNORECASE) if text_pattern else None
    except re.error as exc:
        LOGGER.warning("Invalid undated changelog text pattern %r: %s", text_pattern, exc)
        return []

    anchors: list[Tag] = []
    for selected_tag in selected:
        if selected_tag.name == "a" and selected_tag.get("href"):
            anchors.append(selected_tag)
        else:
            anchors.extend(selected_tag.find_all("a", href=True))

    output: list[_UndatedHtmlEntryLink] = []
    seen: set[str] = set()
    normalized_page = _normalized_url(page_url, page_url)
    for anchor in anchors:
        if _is_next_anchor(anchor):
            continue
        url = _normalized_url(page_url, anchor.get("href"))
        if (
            url is None
            or url == normalized_page
            or url in seen
            or not _same_origin(page_url, url)
        ):
            continue
        if not _path_matches_prefixes(url, path_prefixes):
            continue
        title = _link_title(anchor, anchor)
        if title_pattern and not title_pattern.search(title):
            continue
        seen.add(url)
        output.append(_UndatedHtmlEntryLink(url=url, title=title))
    return output


def _is_allowed_directory_target(
    index_url: str,
    candidate_url: str,
    path_prefixes: tuple[str, ...],
) -> bool:
    """Validate a directory target after redirects have been followed."""

    normalized = _normalized_url(index_url, candidate_url)
    if normalized is None or not _same_origin(index_url, normalized):
        return False
    if normalized == _normalized_url(index_url, index_url):
        return False
    return _path_matches_prefixes(normalized, path_prefixes)


def _configured_detail_dates(
    soup: BeautifulSoup,
    *,
    root_selector: str | None,
) -> list[datetime]:
    """Extract explicit entry dates from headings and table rows in a log page.

    Page-level creation and modification metadata are deliberately excluded.
    They describe the aggregate document, while these dates describe the
    individual updates that ``since`` must filter.
    """

    root: Tag | BeautifulSoup = soup
    if root_selector:
        try:
            selected = soup.select_one(root_selector)
        except Exception as exc:
            LOGGER.warning("Invalid changelog detail selector %r: %s", root_selector, exc)
            selected = None
        if selected is not None:
            root = selected

    # Keep this contract aligned with processing.cleaners: dated headings take
    # precedence because their sections already include any nested tables.
    heading_dates = {
        parsed
        for tag in root.select("h1, h2, h3, h4, h5, h6")
        if (parsed := _parse_date(tag.get_text(" ", strip=True))) is not None
    }
    if heading_dates:
        return sorted(heading_dates)

    row_dates = {
        parsed
        for tag in root.select("tr")
        if tag.find_all("td", recursive=False)
        if (parsed := _parse_date(tag.get_text(" ", strip=True))) is not None
    }
    return sorted(row_dates)


def _extract_next_url(soup: BeautifulSoup, page_url: str) -> str | None:
    anchors = soup.find_all("a", href=True)
    for anchor in anchors:
        rel = anchor.get("rel") or []
        if isinstance(rel, str):
            rel = rel.split()
        if any(str(value).casefold() == "next" for value in rel):
            return _normalized_url(page_url, anchor.get("href"))
    for anchor in anchors:
        if _is_next_anchor(anchor):
            return _normalized_url(page_url, anchor.get("href"))
    return None


def _page_published_at(soup: BeautifulSoup) -> datetime | None:
    for attributes in (
        {"property": "article:published_time"},
        {"name": "publish_date"},
        {"name": "date"},
    ):
        tag = soup.find("meta", attrs=attributes)
        if tag:
            parsed = _parse_date(tag.get("content"))
            if parsed is not None:
                return parsed
    for tag in soup.find_all(["time", "date"], limit=8):
        parsed = next(iter(_dates_in_tag(tag, include_text=True)), None)
        if parsed is not None:
            return parsed
    return None


def _page_modified_at(soup: BeautifulSoup) -> datetime | None:
    """Return the page-level modification time when the publisher exposes it."""

    for attributes in (
        {"property": "article:modified_time"},
        {"property": "og:updated_time"},
        {"name": "last-modified"},
        {"name": "last_modified"},
        {"name": "updated"},
    ):
        tag = soup.find("meta", attrs=attributes)
        if tag:
            parsed = _parse_date(tag.get("content"))
            if parsed is not None:
                return parsed
    for selector in (".update-time", "[data-last-modified]", "[data-updated-at]"):
        for tag in soup.select(selector)[:4]:
            for candidate in (
                tag.get("data-last-modified"),
                tag.get("data-updated-at"),
                tag.get_text(" ", strip=True),
            ):
                parsed = _parse_date(candidate)
                if parsed is not None:
                    return parsed
    return None


class ChangelogCollector(PageCollector):
    """Backfill HTML entry pages and materialize feed entries as raw records."""

    source_type = SourceType.CHANGELOG

    def __init__(self, client, writer, *, rss_loader: RSSLoader | None = None) -> None:
        super().__init__(client, writer)
        self.rss_loader = rss_loader or RSSLoader()

    @staticmethod
    def _is_feed(task: CollectorTask, content_type: str = "") -> bool:
        declared = task.format.strip().lower()
        return declared in {"rss", "atom", "xml", "feed"} or any(
            marker in content_type.lower()
            for marker in ("rss+xml", "atom+xml", "application/xml", "text/xml")
        )

    @staticmethod
    def _cutoff(task: CollectorTask) -> datetime | None:
        return _utc(task.since) if task.since is not None else None

    def _collect_detail(
        self,
        result: CollectorResult,
        task: CollectorTask,
        entry: _HtmlEntryLink,
        *,
        index_url: str,
        page_number: int,
        seen_final_urls: set[str],
    ) -> None:
        metadata = {
            **task.metadata,
            "format": "html",
            "record_role": "changelog_entry",
            "changelog_index_url": index_url,
            "changelog_page_number": page_number,
            "listing_title": entry.title,
            "listing_published_at": isoformat_utc(entry.published_at),
        }
        try:
            response = self.client.get(entry.url, force=task.force)
        except (requests.RequestException, RobotsDeniedError) as exc:
            self._error_record(result, task, entry.url, exc, metadata=metadata)
            return
        final_url = _normalized_url(index_url, response.url or entry.url)
        if final_url is None or not _same_origin(index_url, final_url):
            self._error_record(
                result,
                task,
                entry.url,
                "changelog detail redirected outside listing origin",
                response=response,
                metadata={
                    **metadata,
                    "redirect_target": response.url,
                    "redirect_rejected": True,
                },
            )
            return
        if response.status_code != 304 and not 200 <= response.status_code < 300:
            self._error_record(
                result,
                task,
                entry.url,
                f"HTTP {response.status_code}",
                response=response,
                metadata=metadata,
            )
            return
        if final_url in seen_final_urls:
            duplicate_metadata = {
                **metadata,
                "record_role": "changelog_candidate",
                "candidate_status": "duplicate_final_url",
                "duplicate_final_url": final_url,
            }
            result.records.append(
                self.writer.write_bytes(
                    competitor=task.competitor,
                    source_type=self.source_type,
                    requested_url=entry.url,
                    canonical_url=entry.url,
                    final_url=response.url,
                    payload=None,
                    http_status=response.status_code,
                    content_type=response.headers.get("Content-Type"),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    published_at=entry.published_at,
                    source_metadata=duplicate_metadata,
                )
            )
            return
        seen_final_urls.add(final_url)
        if response.status_code == 304:
            result.records.append(
                self.writer.write_response(
                    competitor=task.competitor,
                    source_type=self.source_type,
                    requested_url=entry.url,
                    canonical_url=final_url,
                    response=response,
                    published_at=entry.published_at,
                    source_metadata=metadata,
                )
            )
            return
        content_type = response.headers.get("Content-Type", "").lower()
        visible_length: int | None = None
        needs_browser = False
        published_at = entry.published_at
        if "html" in content_type or not content_type:
            visible_length = visible_text_length(
                response.content, response.encoding or "utf-8"
            )
            needs_browser = visible_length < self.minimum_visible_characters
            soup = BeautifulSoup(response.content, "html.parser")
            published_at = _page_published_at(soup) or entry.published_at
        metadata["visible_text_length"] = visible_length
        if needs_browser:
            metadata["parse_warning"] = "detail page has insufficient server-rendered text"
        result.records.append(
            self.writer.write_response(
                competitor=task.competitor,
                source_type=self.source_type,
                requested_url=entry.url,
                canonical_url=final_url,
                response=response,
                published_at=published_at,
                needs_browser=needs_browser,
                source_metadata=metadata,
            )
        )

    def _collect_undated_detail(
        self,
        result: CollectorResult,
        task: CollectorTask,
        entry: _UndatedHtmlEntryLink,
        *,
        cutoff: datetime | None,
        index_url: str,
        page_number: int,
        path_prefixes: tuple[str, ...],
        detail_root_selector: str | None,
        seen_final_urls: set[str],
    ) -> str:
        """Fetch an opt-in directory target and filter on explicit content dates."""

        metadata = {
            **task.metadata,
            "format": "html",
            "record_role": "changelog_candidate",
            "discovery_mode": "configured_undated_directory",
            "changelog_index_url": index_url,
            "changelog_page_number": page_number,
            "listing_title": entry.title,
        }
        try:
            response = self.client.get(entry.url, force=task.force)
        except (requests.RequestException, RobotsDeniedError) as exc:
            self._error_record(result, task, entry.url, exc, metadata=metadata)
            return "failed"
        final_url = _normalized_url(index_url, response.url or entry.url)
        if final_url is None or not _is_allowed_directory_target(
            index_url,
            final_url,
            path_prefixes,
        ):
            self._error_record(
                result,
                task,
                entry.url,
                "directory target redirected outside configured scope",
                response=response,
                metadata={
                    **metadata,
                    "candidate_status": "redirect_out_of_scope",
                    "redirect_target": response.url,
                },
            )
            return "redirect_out_of_scope"
        if response.status_code != 304 and not 200 <= response.status_code < 300:
            self._error_record(
                result,
                task,
                entry.url,
                f"HTTP {response.status_code}",
                response=response,
                metadata=metadata,
            )
            return "failed"
        if final_url in seen_final_urls:
            metadata.update(
                {
                    "candidate_status": "duplicate_final_url",
                    "duplicate_final_url": final_url,
                }
            )
            result.records.append(
                self.writer.write_bytes(
                    competitor=task.competitor,
                    source_type=self.source_type,
                    requested_url=entry.url,
                    canonical_url=entry.url,
                    final_url=response.url,
                    payload=None,
                    http_status=response.status_code,
                    content_type=response.headers.get("Content-Type"),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    source_metadata=metadata,
                )
            )
            return "duplicate_final_url"
        seen_final_urls.add(final_url)
        if response.status_code == 304:
            metadata["candidate_status"] = "not_modified"
            result.records.append(
                self.writer.write_response(
                    competitor=task.competitor,
                    source_type=self.source_type,
                    requested_url=entry.url,
                    canonical_url=final_url,
                    response=response,
                    source_metadata=metadata,
                )
            )
            return "not_modified"
        content_type = response.headers.get("Content-Type", "").lower()
        visible_length: int | None = None
        needs_browser = False
        published_at: datetime | None = None
        modified_at: datetime | None = None
        content_dates: list[datetime] = []
        if "html" in content_type or not content_type:
            visible_length = visible_text_length(
                response.content, response.encoding or "utf-8"
            )
            needs_browser = visible_length < self.minimum_visible_characters
            soup = BeautifulSoup(response.content, "html.parser")
            modified_at = _page_modified_at(soup)
            published_at = _page_published_at(soup)
            content_dates = _configured_detail_dates(
                soup,
                root_selector=detail_root_selector,
            )
        effective_date = max(content_dates) if content_dates else None
        metadata.update(
            {
                "visible_text_length": visible_length,
                "page_modified_at": isoformat_utc(modified_at),
                "page_published_at": isoformat_utc(published_at),
                "content_date_count": len(content_dates),
                "content_date_earliest": isoformat_utc(min(content_dates))
                if content_dates
                else None,
                "content_date_latest": isoformat_utc(max(content_dates))
                if content_dates
                else None,
                "cutoff": isoformat_utc(cutoff),
                "date_basis": (
                    "content_latest"
                    if content_dates
                    else None
                ),
            }
        )

        if effective_date is None:
            metadata.update(
                {
                    "candidate_status": "undated",
                    "parse_warning": "directory target has no page-level date",
                }
            )
            status = "undated"
        elif cutoff is not None and effective_date < cutoff:
            metadata["candidate_status"] = "outside_cutoff"
            status = "outside_cutoff"
        else:
            metadata.update(
                {
                    "record_role": "changelog_entry",
                    "candidate_status": "eligible",
                }
            )
            status = "eligible"
        if needs_browser:
            metadata["parse_warning"] = "detail page has insufficient server-rendered text"

        result.records.append(
            self.writer.write_response(
                competitor=task.competitor,
                source_type=self.source_type,
                requested_url=entry.url,
                canonical_url=final_url,
                response=response,
                published_at=effective_date,
                needs_browser=needs_browser,
                source_metadata=metadata,
            )
        )
        return status

    def _collect_html(self, task: CollectorTask) -> CollectorResult:
        result = CollectorResult(task.competitor, self.source_type)
        if not task.enabled:
            result.skipped.append("source disabled")
            return result
        if not task.urls:
            result.skipped.append("no URL configured")
            return result

        cutoff = self._cutoff(task)
        visited_indexes: set[str] = set()
        seen_entries: set[str] = set()
        seen_final_urls: set[str] = set()
        try:
            max_pages = max(1, int(task.metadata.get("max_pages", 20)))
        except (TypeError, ValueError):
            max_pages = 20
        try:
            max_entries = max(1, int(task.metadata.get("max_entries", 200)))
        except (TypeError, ValueError):
            max_entries = 200
        entry_limit_reached = False
        undated_selector = str(task.metadata.get("undated_entry_selector") or "").strip()
        undated_path_prefixes = _metadata_list(
            task.metadata.get("undated_entry_path_prefixes")
        )
        undated_text_pattern = str(
            task.metadata.get("undated_entry_text_pattern") or ""
        ).strip()
        detail_root_selector = str(
            task.metadata.get("undated_detail_root_selector") or ""
        ).strip()

        for seed_url in task.urls:
            if entry_limit_reached:
                break
            current_url = _normalized_url(seed_url, seed_url) or seed_url
            page_number = 1
            while current_url and page_number <= max_pages:
                if current_url in visited_indexes:
                    break
                visited_indexes.add(current_url)
                refreshed_after_304 = False
                try:
                    response = self.client.get(current_url, force=task.force)
                except (requests.RequestException, RobotsDeniedError) as exc:
                    self._error_record(
                        result,
                        task,
                        current_url,
                        exc,
                        metadata={
                            **task.metadata,
                            "record_role": "changelog_index",
                            "changelog_page_number": page_number,
                        },
                    )
                    break

                response_final_url = _normalized_url(
                    current_url,
                    response.url or current_url,
                )
                if response_final_url is None or not _same_origin(
                    current_url,
                    response_final_url,
                ):
                    self._error_record(
                        result,
                        task,
                        current_url,
                        "changelog index redirected outside configured origin",
                        response=response,
                        metadata={
                            **task.metadata,
                            "record_role": "changelog_index",
                            "changelog_page_number": page_number,
                            "redirect_target": response.url,
                            "redirect_rejected": True,
                        },
                    )
                    break

                if response.status_code == 304:
                    not_modified_metadata = {
                        **task.metadata,
                        "format": "html_changelog_index",
                        "record_role": "changelog_index",
                        "changelog_page_number": page_number,
                        "cutoff": isoformat_utc(cutoff),
                        "directory_refresh_required": bool(undated_selector),
                    }
                    result.records.append(
                        self.writer.write_response(
                            competitor=task.competitor,
                            source_type=self.source_type,
                            requested_url=current_url,
                            canonical_url=response_final_url,
                            response=response,
                            source_metadata=not_modified_metadata,
                        )
                    )
                    if not undated_selector:
                        break
                    # The directory can remain unchanged while its child log
                    # pages receive independent updates. Refresh the small,
                    # explicitly configured index once so those child URLs are
                    # still checked with their own conditional requests.
                    try:
                        response = self.client.get(current_url, force=True)
                    except (requests.RequestException, RobotsDeniedError) as exc:
                        self._error_record(
                            result,
                            task,
                            current_url,
                            exc,
                            metadata={
                                **task.metadata,
                                "record_role": "changelog_index",
                                "changelog_page_number": page_number,
                                "directory_refresh_after_304": True,
                            },
                        )
                        break
                    refreshed_after_304 = True
                    response_final_url = _normalized_url(
                        current_url,
                        response.url or current_url,
                    )
                    if response_final_url is None or not _same_origin(
                        current_url,
                        response_final_url,
                    ):
                        self._error_record(
                            result,
                            task,
                            current_url,
                            "refreshed changelog index redirected outside configured origin",
                            response=response,
                            metadata={
                                **task.metadata,
                                "record_role": "changelog_index",
                                "changelog_page_number": page_number,
                                "directory_refresh_after_304": True,
                                "redirect_target": response.url,
                                "redirect_rejected": True,
                            },
                        )
                        break
                    if response.status_code == 304:
                        self._error_record(
                            result,
                            task,
                            current_url,
                            "unconditional directory refresh returned 304 without payload",
                            response=response,
                            metadata={
                                **task.metadata,
                                "record_role": "changelog_index",
                                "changelog_page_number": page_number,
                                "directory_refresh_after_304": True,
                            },
                        )
                        break
                if not 200 <= response.status_code < 300:
                    self._error_record(
                        result,
                        task,
                        current_url,
                        f"HTTP {response.status_code}",
                        response=response,
                        metadata={
                            **task.metadata,
                            "record_role": "changelog_index",
                            "changelog_page_number": page_number,
                            "directory_refresh_after_304": refreshed_after_304,
                        },
                    )
                    break

                soup = BeautifulSoup(response.content, "html.parser")
                page_url = response_final_url
                discovered_entries = _extract_entry_links(soup, page_url)
                entries = [
                    entry
                    for entry in discovered_entries
                    if _same_origin(page_url, entry.url)
                ]
                undated_entries = _extract_configured_undated_links(
                    soup,
                    page_url,
                    selector=undated_selector or None,
                    path_prefixes=undated_path_prefixes,
                    text_pattern=undated_text_pattern or None,
                )
                rejected_cross_origin_entries = len(discovered_entries) - len(entries)
                next_url = _extract_next_url(soup, page_url)
                rejected_next_url: str | None = None
                if next_url and not _same_origin(page_url, next_url):
                    rejected_next_url = next_url
                    next_url = None
                eligible = [
                    entry
                    for entry in entries
                    if cutoff is None or entry.published_at >= cutoff
                ]
                visible_length = visible_text_length(
                    response.content, response.encoding or "utf-8"
                )
                parse_warning = (
                    "no changelog entry links found"
                    if not entries and not undated_entries
                    else None
                )
                configured_empty_markers = task.metadata.get(
                    "empty_result_markers",
                    [],
                )
                if isinstance(configured_empty_markers, str):
                    configured_empty_markers = [configured_empty_markers]
                page_text = soup.get_text(" ", strip=True).casefold()
                matched_empty_marker = next(
                    (
                        str(marker)
                        for marker in configured_empty_markers
                        if str(marker).strip()
                        and str(marker).strip().casefold() in page_text
                    ),
                    None,
                )
                empty_result_valid = bool(
                    not entries
                    and not undated_entries
                    and matched_empty_marker
                )
                needs_browser = (
                    visible_length < self.minimum_visible_characters
                    or (
                        not entries
                        and not undated_entries
                        and not empty_result_valid
                    )
                )
                undated_status_counts: dict[str, int] = {}
                attempted_undated = 0
                for entry in undated_entries:
                    if entry.url in seen_entries or entry.url in seen_final_urls:
                        continue
                    if len(seen_entries) >= max_entries:
                        entry_limit_reached = True
                        break
                    seen_entries.add(entry.url)
                    attempted_undated += 1
                    status = self._collect_undated_detail(
                        result,
                        task,
                        entry,
                        cutoff=cutoff,
                        index_url=page_url,
                        page_number=page_number,
                        path_prefixes=undated_path_prefixes,
                        detail_root_selector=detail_root_selector or None,
                        seen_final_urls=seen_final_urls,
                    )
                    undated_status_counts[status] = undated_status_counts.get(status, 0) + 1
                index_metadata = {
                    **task.metadata,
                    "format": "html_changelog_index",
                    "record_role": "changelog_index",
                    "changelog_page_number": page_number,
                    "dated_entry_count": len(entries),
                    "eligible_entry_count": len(eligible),
                    "configured_undated_entry_count": len(undated_entries),
                    "attempted_undated_entry_count": attempted_undated,
                    "undated_entry_status_counts": undated_status_counts,
                    "rejected_cross_origin_entry_count": rejected_cross_origin_entries,
                    "visible_text_length": visible_length,
                    "cutoff": isoformat_utc(cutoff),
                    "next_url": next_url,
                    "max_entries": max_entries,
                    "directory_refresh_after_304": refreshed_after_304,
                    "empty_result_valid": empty_result_valid,
                    "empty_result_marker": matched_empty_marker,
                }
                if parse_warning and not empty_result_valid:
                    index_metadata["parse_warning"] = parse_warning
                if rejected_next_url:
                    index_metadata["rejected_cross_origin_next_url"] = rejected_next_url
                result.records.append(
                    self.writer.write_response(
                        competitor=task.competitor,
                        source_type=self.source_type,
                        requested_url=current_url,
                        canonical_url=current_url,
                        response=response,
                        needs_browser=needs_browser,
                        source_metadata=index_metadata,
                    )
                )

                for entry in eligible:
                    if entry.url in seen_entries:
                        continue
                    if len(seen_entries) >= max_entries:
                        entry_limit_reached = True
                        break
                    seen_entries.add(entry.url)
                    self._collect_detail(
                        result,
                        task,
                        entry,
                        index_url=response.url or current_url,
                        page_number=page_number,
                        seen_final_urls=seen_final_urls,
                    )

                # A pinned old entry can appear before newer entries.  Stop at
                # the normal chronological boundary (the remaining entries
                # after the first old item are also old), but continue when a
                # newer item appears later on the same page.
                in_window = [
                    cutoff is None or entry.published_at >= cutoff
                    for entry in entries
                ]
                first_old = next(
                    (index for index, eligible_item in enumerate(in_window) if not eligible_item),
                    None,
                )
                encountered_cutoff = bool(
                    cutoff
                    and first_old is not None
                    and not any(in_window[first_old + 1 :])
                )
                if (
                    entry_limit_reached
                    or encountered_cutoff
                    or not next_url
                    or next_url in visited_indexes
                ):
                    break
                current_url = next_url
                page_number += 1

        return result

    def _collect_feed(self, task: CollectorTask) -> CollectorResult:
        result = CollectorResult(task.competitor, self.source_type)
        if not task.enabled:
            result.skipped.append("source disabled")
            return result
        if not task.urls:
            result.skipped.append("no URL configured")
            return result

        cutoff = self._cutoff(task)
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
                        source_metadata={**task.metadata, "format": "feed"},
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

            items = self.rss_loader.parse(response.content, response.url or url)
            if not items:
                result.records.append(
                    self.writer.write_response(
                        competitor=task.competitor,
                        source_type=self.source_type,
                        requested_url=url,
                        response=response,
                        needs_browser=False,
                        source_metadata={
                            **task.metadata,
                            "format": "feed",
                            "record_role": "feed_empty",
                            "entry_count": 0,
                            "parse_warning": "no feed entries found",
                        },
                    )
                )
                continue

            for item in items:
                event_time = item.published_at or item.updated_at
                if cutoff and event_time and _utc(event_time) < cutoff:
                    continue
                entry_kind = (
                    "rss_entry"
                    if self.source_type is SourceType.RSS
                    else "changelog_entry"
                )
                result.records.append(
                    self.writer.write_json(
                        competitor=task.competitor,
                        source_type=self.source_type,
                        requested_url=url,
                        canonical_url=item.link or item.id or url,
                        final_url=response.url,
                        http_status=response.status_code,
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                        published_at=event_time,
                        payload={
                            "kind": entry_kind,
                            "feed_url": response.url or url,
                            "entry": item.to_dict(),
                        },
                        source_metadata={
                            **task.metadata,
                            "format": "feed_entry",
                            "record_role": entry_kind,
                            "entry_id": item.id,
                            "original_content_type": response.headers.get("Content-Type"),
                        },
                    )
                )
        return result

    def collect(self, task: CollectorTask) -> CollectorResult:
        if task.format.strip().lower() == "json":
            return super().collect(task)
        if self._is_feed(task):
            return self._collect_feed(task)
        return self._collect_html(task)


class RSSCollector(ChangelogCollector):
    """Collect an RSS or Atom source as stable, cutoff-aware entry records."""

    source_type = SourceType.RSS

    def collect(self, task: CollectorTask) -> CollectorResult:
        return self._collect_feed(task)


ChangelogCrawler = ChangelogCollector


__all__ = ["ChangelogCollector", "ChangelogCrawler", "RSSCollector"]
