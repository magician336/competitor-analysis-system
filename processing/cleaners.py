"""Source-aware payload cleaning and item extraction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Comment, UnicodeDammit
from bs4.element import Declaration, Doctype, NavigableString, ProcessingInstruction, Tag
from dateutil import parser as date_parser

from schemas.document import RawRecord, SourceType

from .normalizers import (
    extract_version,
    normalize_datetime,
    normalize_text,
    normalize_url,
    normalize_version,
)


_REMOVED_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "canvas",
    "template",
    "iframe",
    "nav",
    "footer",
}

_BOILERPLATE_PATTERN = re.compile(
    r"(?i)^(?:accept (?:all )?cookies?|cookie (?:settings|preferences|policy)|"
    r"privacy preferences|skip to (?:main )?content|back to top|"
    r"同意(?:所有)?(?: Cookie|饼干)|Cookie 设置|返回顶部)$"
)

_COOKIE_CONTAINER_PATTERN = re.compile(
    r"(?i)(?:^|[-_\s])(?:cookie|consent)[-_\s]?"
    r"(?:banner|bar|box|consent|dialog|modal|notice|overlay|popup|preferences|settings|widget)(?:$|[-_\s])"
)

_CURSOR_CHANGELOG_LEAD_PATTERN = re.compile(
    r"(?ims)\A(?P<prefix>.{1,240}?)(?=^#[ \t]+\S)"
)

_CHANGELOG_DATE_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b",
        r"\b20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?\b",
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+20\d{2}\b",
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[,]?\s+20\d{2}\b",
    )
)
_MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_STABLE_CHANGELOG_TABLE_HEADERS = {
    "releaseid",
    "releasetitle",
    "title",
    "version",
    "versionid",
    "公告标题",
    "发布版本",
    "更新标题",
    "标题",
    "版本",
    "版本号",
    "版本名称",
}

_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "details",
    "div",
    "dl",
    "dt",
    "dd",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "table",
    "ul",
}

_INLINE_EXCLUDED_TAGS = {"ol", "table", "ul"}


@dataclass(slots=True)
class CleanedItem:
    """One logical document extracted from a raw response."""

    title: str
    content: str
    url: str | None = None
    publish_time: datetime | None = None
    author: str | None = None
    raw_version: str | None = None
    product_version: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


def _deduplicate_lines(text: str) -> str:
    output: list[str] = []
    previous_nonempty: str | None = None
    raw_text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    for line in raw_text.splitlines():
        leading = line[: len(line) - len(line.lstrip(" \t"))]
        stripped = normalize_text(line, preserve_lines=False)
        if not stripped:
            if output and output[-1] != "":
                output.append("")
            continue
        if _BOILERPLATE_PATTERN.match(stripped):
            continue
        folded = stripped.casefold()
        if folded == previous_nonempty:
            continue
        if leading and re.match(r"^(?:[-+*]|\d+\.)\s", stripped):
            indent_width = sum(2 if character == "\t" else 1 for character in leading)
            output.append(f"{' ' * indent_width}{stripped}")
        else:
            output.append(stripped)
        previous_nonempty = folded
    return "\n".join(output).strip()


def _prepare_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html or "", "html.parser")
    non_content_nodes = (Comment, Declaration, Doctype, ProcessingInstruction)
    for node in soup.find_all(string=lambda value: isinstance(value, non_content_nodes)):
        node.extract()
    for tag in soup.find_all(_REMOVED_TAGS):
        tag.decompose()
    for selector in (
        "[aria-hidden='true']",
        "[hidden]",
        "[inert]",
        "[role='navigation']",
        "[role='banner']",
        "[role='contentinfo']",
        ".d-none",
        ".cookie-banner",
        ".cookie-consent",
        "#cookie-banner",
        "#cookie-consent",
    ):
        for tag in soup.select(selector):
            tag.decompose()
    # Cookie/consent widgets use many framework-specific class names.  Match
    # only container-like names so an article discussing a cookie policy is
    # not accidentally discarded.
    for tag in list(soup.find_all(True)):
        if tag.attrs is None:
            continue
        classes = tag.get("class", [])
        class_text = classes if isinstance(classes, str) else " ".join(map(str, classes))
        attributes = " ".join(
            filter(
                None,
                (
                    str(tag.get("id", "") or ""),
                    class_text,
                    str(tag.get("aria-label", "") or ""),
                ),
            )
        )
        if _COOKIE_CONTAINER_PATTERN.search(attributes):
            tag.decompose()
    return soup


def _drop_duplicate_responsive_faq(soup: BeautifulSoup) -> None:
    """Keep one complete FAQ representation when mobile and tab UIs coexist."""

    for tablist in list(soup.select("[role='tablist']")):
        for ancestor in tablist.parents:
            if not isinstance(ancestor, Tag):
                continue
            accordion = ancestor.select_one(
                "[class*='FAQGroup'][class*='accordion']"
            )
            panels = ancestor.select("[role='tabpanel']")
            if accordion is None or not panels:
                continue
            accordion_text = normalize_text(
                accordion.get_text(" ", strip=True),
                preserve_lines=False,
            )
            panel_text = normalize_text(
                " ".join(panel.get_text(" ", strip=True) for panel in panels),
                preserve_lines=False,
            )
            # The accordion must contain at least as much substantive content
            # as the active tab representation.  This prevents ordinary tab
            # widgets from being removed merely because an accordion exists
            # elsewhere on the page.
            if len(accordion_text) < max(200, int(len(panel_text) * 0.8)):
                break
            tablist.decompose()
            for panel in panels:
                panel.decompose()
            break


def _inline_text(tag: Tag, *, excluded: set[str] | None = None) -> str:
    """Return inline text without consuming nested lists or tables."""

    excluded_names = _INLINE_EXCLUDED_TAGS if excluded is None else excluded
    fragments: list[str] = []

    def visit(node: Tag | NavigableString) -> None:
        if isinstance(node, NavigableString):
            fragments.append(str(node))
            return
        name = (node.name or "").lower()
        if name in excluded_names:
            return
        if name == "br":
            fragments.append("\n")
            return
        for child in node.children:
            if isinstance(child, (Tag, NavigableString)):
                visit(child)

    visit(tag)
    return normalize_text("".join(fragments), preserve_lines=False)


def _render_list(list_tag: Tag, *, depth: int = 0) -> list[str]:
    """Render one HTML list while retaining order and nesting."""

    lines: list[str] = []
    ordered = list_tag.name.lower() == "ol"
    start_value = list_tag.get("start", 1)
    try:
        start = int(start_value)
    except (TypeError, ValueError):
        start = 1

    items = list_tag.find_all("li", recursive=False)
    for offset, item in enumerate(items):
        marker = f"{start + offset}." if ordered else "-"
        text = _inline_text(item)
        prefix = f"{'  ' * depth}{marker}"
        if text:
            lines.append(f"{prefix} {text}")
        else:
            lines.append(prefix)
        for nested in item.find_all(["ol", "ul"]):
            nearest_item = nested.find_parent("li")
            nearest_list = nested.find_parent(["ol", "ul"])
            if nearest_item is item and nearest_list is list_tag:
                lines.extend(_render_list(nested, depth=depth + 1))
    return lines


def _table_cell_text(cell: Tag) -> str:
    blocks = _render_blocks(cell)
    value = "<br>".join(block.replace("\n", "<br>") for block in blocks)
    if not value:
        value = _inline_text(cell, excluded=set())
    return value.replace("|", r"\|")


def _render_table(table: Tag) -> list[str]:
    """Render an HTML table as deterministic Markdown rows and columns."""

    rows: list[tuple[list[str], bool]] = []
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        values: list[str] = []
        for cell in cells:
            values.append(_table_cell_text(cell))
            try:
                colspan = max(1, int(cell.get("colspan", 1)))
            except (TypeError, ValueError):
                colspan = 1
            values.extend([""] * (colspan - 1))
        rows.append((values, any(cell.name.lower() == "th" for cell in cells)))

    if not rows:
        fallback = _inline_text(table, excluded=set())
        return [fallback] if fallback else []

    width = max(len(values) for values, _ in rows)
    padded = [values + [""] * (width - len(values)) for values, _ in rows]
    first_is_header = rows[0][1]
    output: list[str] = []
    if first_is_header:
        output.append(f"| {' | '.join(padded[0])} |")
        data_rows = padded[1:]
    else:
        output.append(f"| {' | '.join([''] * width)} |")
        data_rows = padded
    output.append(f"| {' | '.join(['---'] * width)} |")
    output.extend(f"| {' | '.join(values)} |" for values in data_rows)
    return output


def _render_blocks(parent: Tag) -> list[str]:
    """Render child nodes exactly once into stable Markdown-like blocks."""

    blocks: list[str] = []
    pending_inline: list[str] = []

    def flush_inline() -> None:
        text = normalize_text("".join(pending_inline), preserve_lines=False)
        pending_inline.clear()
        if text:
            blocks.append(text)

    for child in parent.children:
        if isinstance(child, NavigableString):
            pending_inline.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue

        name = (child.name or "").lower()
        if name == "br":
            pending_inline.append("\n")
            continue
        if name not in _BLOCK_TAGS and child.find(_BLOCK_TAGS):
            flush_inline()
            blocks.extend(_render_blocks(child))
            continue
        if name not in _BLOCK_TAGS:
            pending_inline.append(_inline_text(child, excluded=set()))
            continue

        flush_inline()
        if re.fullmatch(r"h[1-6]", name):
            text = _inline_text(child)
            if text:
                blocks.append(f"{'#' * int(name[1])} {text}")
        elif name == "p":
            text = _inline_text(child)
            if text:
                blocks.append(text)
        elif name in {"ol", "ul"}:
            lines = _render_list(child)
            if lines:
                blocks.append("\n".join(lines))
        elif name == "table":
            lines = _render_table(child)
            if lines:
                blocks.append("\n".join(lines))
        elif name == "blockquote":
            quoted = _render_blocks(child)
            if quoted:
                blocks.append("\n".join(f"> {line}" for block in quoted for line in block.splitlines()))
        elif name == "pre":
            text = normalize_text(child.get_text("", strip=False))
            if text:
                blocks.append(f"```\n{text}\n```")
        elif name == "hr":
            blocks.append("---")
        else:
            blocks.extend(_render_blocks(child))

    flush_inline()
    return blocks


def _drop_duplicate_blocks(blocks: list[str]) -> list[str]:
    """Drop repeated rendered blocks while preserving their first position."""

    output: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        identity = normalize_text(block, preserve_lines=False).casefold()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        output.append(block)
    return output


def clean_html(
    html: str,
    *,
    prefer_article: bool = False,
    deduplicate_blocks: bool = False,
    root_selector: str | None = None,
) -> str:
    """Extract stable Markdown-like text while preserving document structure."""

    soup = _prepare_soup(html)
    _drop_duplicate_responsive_faq(soup)
    article = None
    if prefer_article:
        candidates = [item for item in soup.find_all("article") if item.find("h1")]
        if candidates:
            article = max(candidates, key=lambda item: len(item.get_text(" ", strip=True)))
    selected_root = soup.select_one(root_selector) if root_selector else None
    root = selected_root or article or soup.find("main") or soup.find("article") or soup.body or soup
    if selected_root is None and article is None and len(root.get_text(" ", strip=True)) < 80:
        candidates = [
            candidate
            for candidate in (article, soup.find("main"), soup.find("article"), soup.body, soup)
            if candidate is not None
        ]
        fallback = max(
            candidates,
            key=lambda candidate: len(candidate.get_text(" ", strip=True)),
        )
        if len(fallback.get_text(" ", strip=True)) >= 80:
            root = fallback
    blocks = _render_blocks(root)
    if deduplicate_blocks:
        blocks = _drop_duplicate_blocks(blocks)
    return _deduplicate_lines("\n\n".join(blocks))


def _strip_changelog_page_lead(content: str) -> str:
    """Remove Cursor's date/changelog page chrome before the article heading."""

    match = _CURSOR_CHANGELOG_LEAD_PATTERN.match(content)
    if match is None:
        return content
    prefix = match.group("prefix")
    has_dated_label = re.search(
        r"(?im)^[^\r\n]{0,160}·[ \t]*Changelog[ \t]*$",
        prefix,
    )
    has_section_label = re.search(r"(?im)^Changelog[ \t]*$", prefix)
    if not has_dated_label or not has_section_label:
        return content
    return content[match.end() :].lstrip()


def _flatten_json(value: Any, *, key: str | None = None) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, dict):
        preferred = (
            "title",
            "name",
            "tag_name",
            "state",
            "body",
            "content",
            "description",
            "summary",
            "text",
        )
        emitted: set[str] = set()
        for candidate in preferred:
            if candidate in value:
                emitted.add(candidate)
                yield from _flatten_json(value[candidate], key=candidate)
        for child_key in sorted(value):
            if child_key in emitted or child_key in {
                "id",
                "node_id",
                "url",
                "html_url",
                "avatar_url",
                "events_url",
                "repository_url",
            }:
                continue
            yield from _flatten_json(value[child_key], key=child_key)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten_json(item, key=key)
    elif isinstance(value, bool):
        if key:
            yield f"{key}: {str(value).lower()}"
    elif isinstance(value, (str, int, float)):
        text = normalize_text(unescape(str(value)))
        if "<" in text and ">" in text:
            text = clean_html(text)
        if text:
            yield text


def clean_json(payload: str | bytes | dict[str, Any] | list[Any]) -> str:
    """Convert a JSON payload to deterministic searchable text."""

    value: Any = payload
    if isinstance(payload, bytes):
        value = payload.decode("utf-8-sig", errors="replace")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return normalize_text(value)
    return _deduplicate_lines("\n".join(_flatten_json(value)))


_RICH_TEXT_COMMENT_PATTERN = re.compile(r"<!--[\s\S]*?-->")
_RICH_TEXT_UNSAFE_PATTERN = re.compile(
    r"(?is)<(?:script|style)\b[^>]*>[\s\S]*?</(?:script|style)\s*>"
)
_RICH_TEXT_BREAK_PATTERN = re.compile(r"(?i)<br\s*/?>")
_RICH_TEXT_BLOCK_PATTERN = re.compile(
    r"(?i)</?(?:details|summary|p|div|section|article|h[1-6]|ul|ol|li|table|tr|blockquote|pre)\b[^>]*>"
)
_RICH_TEXT_IMAGE_PATTERN = re.compile(r"(?is)<img\b[^>]*>")


def _clean_rich_text(value: Any) -> str:
    """Clean Markdown with embedded HTML without discarding Markdown text."""

    text = str(value or "")
    text = _RICH_TEXT_COMMENT_PATTERN.sub("", text)
    text = _RICH_TEXT_UNSAFE_PATTERN.sub("", text)
    text = _RICH_TEXT_BREAK_PATTERN.sub("\n", text)

    def image_alt(match: re.Match[str]) -> str:
        tag = BeautifulSoup(match.group(0), "html.parser").find("img")
        if tag is None:
            return ""
        alt = normalize_text(tag.get("alt", ""), preserve_lines=False)
        return f"[Image: {alt}]" if alt else ""

    text = _RICH_TEXT_IMAGE_PATTERN.sub(image_alt, text)
    text = _RICH_TEXT_BLOCK_PATTERN.sub("\n", text)
    return _deduplicate_lines(unescape(text))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ElementTree.Element, names: set[str]) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) in names:
            value = "".join(child.itertext()).strip()
            if value:
                return value
    return None


def _rss_entries(payload: str | bytes) -> list[CleanedItem]:
    raw = payload.decode("utf-8-sig", errors="replace") if isinstance(payload, bytes) else payload
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return []
    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    output: list[CleanedItem] = []
    for entry in entries:
        title = _child_text(entry, {"title"}) or "Untitled feed entry"
        link = _child_text(entry, {"link", "id"})
        if not link:
            for child in entry.iter():
                if _local_name(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        body = _child_text(entry, {"content", "encoded", "description", "summary"}) or ""
        body = clean_html(body) if "<" in body and ">" in body else normalize_text(body)
        author = _child_text(entry, {"author", "creator", "name"})
        published = _child_text(entry, {"published", "updated", "pubdate", "date"})
        raw_version, product_version = extract_version(title, body)
        output.append(
            CleanedItem(
                title=normalize_text(title, preserve_lines=False),
                content=body,
                url=normalize_url(link or "") or None,
                publish_time=normalize_datetime(published),
                author=normalize_text(author, preserve_lines=False) if author else None,
                raw_version=raw_version,
                product_version=product_version,
            )
        )
    return output


def clean_rss(payload: str | bytes) -> str:
    """Convert RSS/Atom entries into a readable text block."""

    entries = _rss_entries(payload)
    return "\n\n".join(
        part for entry in entries for part in (entry.title, entry.content) if part
    ).strip()


_BARE_VERSION_PATTERN = re.compile(
    r"(?i)^v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?$"
)

_CHARSET_PATTERN = re.compile(
    r"(?i)(?:^|;)\s*charset\s*=\s*[\"']?([^;\s\"']+)"
)


def _decode_html(payload: bytes, content_type: str) -> str:
    """Decode HTML using the HTTP charset and in-document declarations."""

    match = _CHARSET_PATTERN.search(content_type)
    declared = match.group(1).strip() if match else None
    if declared:
        try:
            return payload.decode(declared, errors="strict")
        except (LookupError, UnicodeDecodeError):
            pass
    decoded = UnicodeDammit(
        payload,
        known_definite_encodings=[declared] if declared else None,
        is_html=True,
    ).unicode_markup
    if decoded is not None:
        return decoded
    return payload.decode("utf-8-sig", errors="replace")


_NEXT_RSC_PUSH_PATTERN = re.compile(
    r"self\.__next_f\.push\(\[1,(?P<payload>.*)\]\)",
    flags=re.DOTALL,
)
_NEXT_RSC_CHILD_PATTERN = re.compile(
    r'"children":"(?P<text>(?:\\.|[^"\\])*)"'
)
_NEXT_RSC_IGNORED_TEXT = {
    "404: this page could not be found.",
    "this page could not be found.",
    "this page was not found",
    "return home",
    "report an issue",
}


def _cursor_next_rsc_content(html: str, title: str) -> str:
    """Extract reader-facing strings from Cursor's Next.js RSC payload."""

    soup = BeautifulSoup(html or "", "html.parser")
    lines: list[str] = []
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text() or ""
        if "self.__next_f.push([1," not in script_text:
            continue
        match = _NEXT_RSC_PUSH_PATTERN.fullmatch(script_text.strip())
        if match is None:
            continue
        try:
            decoded = json.loads(match.group("payload"))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(decoded, str):
            continue
        for child_match in _NEXT_RSC_CHILD_PATTERN.finditer(decoded):
            try:
                value = json.loads(f'"{child_match.group("text")}"')
            except json.JSONDecodeError:
                continue
            text = normalize_text(value, preserve_lines=False)
            folded = text.casefold()
            if (
                not text
                or text.startswith("$")
                or folded in _NEXT_RSC_IGNORED_TEXT
                or (
                    len(text) > 180
                    and any(
                        token in text
                        for token in (
                            "document.",
                            "window.",
                            "function(",
                            "getElementsByTagName",
                        )
                    )
                )
            ):
                continue
            lines.append(text)
    content = _deduplicate_lines("\n\n".join(lines))
    if title and content and not content.casefold().startswith(title.casefold()):
        content = f"# {title}\n\n{content}"
    return content


def _trae_router_document(html: str, base_url: str) -> CleanedItem | None:
    """Extract the current document from TRAE Docs' embedded router state."""

    marker = "window._ROUTER_DATA = "
    soup = BeautifulSoup(html or "", "html.parser")
    router_data: dict[str, Any] | None = None
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text() or ""
        if marker not in script_text:
            continue
        raw = script_text.split(marker, 1)[1].strip().rstrip(";")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            router_data = parsed
            break
    if router_data is None:
        return None

    loader_data = router_data.get("loaderData")
    if not isinstance(loader_data, dict):
        return None
    layout = loader_data.get("layout")
    if not isinstance(layout, dict):
        layout = loader_data.get("$")
    if not isinstance(layout, dict):
        return None
    detail = layout.get("docDetail")
    if not isinstance(detail, dict):
        return None

    inserts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            insert = value.get("insert")
            if isinstance(insert, str):
                inserts.append(insert)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(detail.get("content"))
    body = normalize_text("".join(inserts))
    title = normalize_text(
        detail.get("title") or "TRAE Documentation",
        preserve_lines=False,
    )
    if not body:
        return None
    content = f"# {title}\n\n{body}" if title else body
    raw_version, product_version = extract_version(title, body[:1_000])
    return CleanedItem(
        title=title,
        content=content,
        url=normalize_url(base_url),
        publish_time=normalize_datetime(
            detail.get("publish_at") or detail.get("updated_at")
        ),
        raw_version=raw_version,
        product_version=product_version,
        source_metadata={
            "embedded_content_extraction": "trae_router_document",
            "publisher_document_id": detail.get("_id"),
            "publisher_version_id": detail.get("version_id"),
        },
    )


def _html_version(
    soup: BeautifulSoup,
    title: str,
    content: str,
) -> tuple[str | None, str | None]:
    raw_version, product_version = extract_version(title, content[:1_000])
    if raw_version:
        return raw_version, product_version
    for tag in soup.select("[data-version], [class~='version'], [class~='label']"):
        candidates = (tag.get("data-version"), tag.get_text(" ", strip=True))
        for candidate in candidates:
            value = normalize_text(candidate, preserve_lines=False)
            if value and _BARE_VERSION_PATTERN.fullmatch(value):
                return value, normalize_version(value)
    return None, None


def _html_metadata(
    html: str,
    base_url: str,
    source_type: SourceType,
    *,
    embedded_mode: str | None = None,
) -> CleanedItem:
    soup = _prepare_soup(html)

    def meta_value(*keys: tuple[str, str]) -> str | None:
        for attribute, value in keys:
            tag = soup.find("meta", attrs={attribute: value})
            if tag and tag.get("content"):
                return normalize_text(tag["content"], preserve_lines=False)
        return None

    title = None
    if source_type == SourceType.OFFICIAL_CHANGELOG:
        article_candidates = [
            article for article in soup.find_all("article") if article.find("h1")
        ]
        if article_candidates:
            primary_article = max(
                article_candidates,
                key=lambda article: len(article.get_text(" ", strip=True)),
            )
            heading = primary_article.find("h1")
            if heading is not None:
                title = normalize_text(
                    heading.get_text(" ", strip=True),
                    preserve_lines=False,
                )
    if not title:
        title = meta_value(("property", "og:title"), ("name", "twitter:title"))
    if not title and soup.h1:
        title = normalize_text(soup.h1.get_text(" ", strip=True), preserve_lines=False)
    if not title and soup.title:
        title = normalize_text(soup.title.get_text(" ", strip=True), preserve_lines=False)
    title = title or "Untitled page"
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    url = urljoin(base_url, canonical.get("href")) if canonical and canonical.get("href") else base_url
    published = meta_value(
        *((
            ("property", "article:modified_time"),
            ("property", "og:updated_time"),
            ("name", "last-modified"),
            ("name", "last_modified"),
            ("name", "updated"),
        ) if source_type == SourceType.OFFICIAL_CHANGELOG else ()),
        ("property", "article:published_time"),
        ("name", "date"),
        ("name", "publish_date"),
    )
    if not published and source_type == SourceType.OFFICIAL_CHANGELOG:
        time_tag = soup.find("time", attrs={"datetime": True})
        published = time_tag.get("datetime") if time_tag else None
    author = meta_value(("name", "author"), ("property", "article:author"))
    prefer_article = source_type == SourceType.OFFICIAL_CHANGELOG
    is_aliyun_help = urlparse(base_url).hostname == "help.aliyun.com"
    content = clean_html(
        html,
        prefer_article=prefer_article,
        deduplicate_blocks=source_type
        in {SourceType.OFFICIAL_PAGE, SourceType.OFFICIAL_CHANGELOG},
        root_selector=(
            ".aliyun-docs-content .markdown-body"
            if is_aliyun_help
            else None
        ),
    )
    if is_aliyun_help and title and not content.startswith(f"# {title}"):
        content = f"# {title}\n\n{content}" if content else f"# {title}"
    if source_type == SourceType.OFFICIAL_CHANGELOG:
        content = _strip_changelog_page_lead(content)
        raw_version, product_version = _html_version(soup, title, content)
    else:
        raw_version, product_version = extract_version(title)
    source_metadata: dict[str, Any] = {}
    if embedded_mode == "cursor_next_rsc" and len(content) < 80:
        embedded_content = _cursor_next_rsc_content(html, title)
        if embedded_content:
            content = embedded_content
            source_metadata["embedded_content_extraction"] = embedded_mode
    return CleanedItem(
        title=title,
        content=content,
        url=normalize_url(url),
        publish_time=normalize_datetime(published),
        author=author,
        raw_version=raw_version,
        product_version=product_version,
        source_metadata=source_metadata,
    )


def _explicit_changelog_date(value: Any) -> datetime | None:
    """Parse a date only when the text contains an explicit calendar day."""

    text = normalize_text(value, preserve_lines=False)
    match = next(
        (pattern.search(text) for pattern in _CHANGELOG_DATE_PATTERNS if pattern.search(text)),
        None,
    )
    if match is None:
        return None
    candidate = re.sub(
        r"(\d{1,2})(?:st|nd|rd|th)",
        r"\1",
        match.group(0),
        flags=re.IGNORECASE,
    )
    candidate = candidate.replace("年", "-").replace("月", "-").replace("日", "")
    try:
        parsed = date_parser.parse(candidate, fuzzy=False)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date_heading_identity_text(value: str) -> str:
    text = normalize_text(value, preserve_lines=False)
    for pattern in _CHANGELOG_DATE_PATTERNS:
        if pattern.search(text):
            text = pattern.sub(" ", text, count=1)
            break
    return normalize_text(text.strip(" -—–:：()（）[]【】"), preserve_lines=False).casefold()


def _dated_heading_items(
    base: CleanedItem,
    *,
    cutoff: datetime | None,
) -> list[CleanedItem]:
    lines = base.content.splitlines()
    markers: list[tuple[int, str, datetime]] = []
    for index, line in enumerate(lines):
        heading = _MARKDOWN_HEADING_PATTERN.match(line)
        if heading is None:
            continue
        published_at = _explicit_changelog_date(heading.group(2))
        if published_at is not None:
            markers.append((index, normalize_text(heading.group(2), preserve_lines=False), published_at))
    if not markers:
        return []

    occurrences: dict[tuple[str, str], int] = {}
    output: list[CleanedItem] = []
    for marker_index, (start, heading, published_at) in enumerate(markers):
        if cutoff is not None and published_at < cutoff:
            continue
        end = markers[marker_index + 1][0] if marker_index + 1 < len(markers) else len(lines)
        section = normalize_text("\n".join(lines[start:end]))
        body_without_heading = normalize_text("\n".join(lines[start + 1 : end]))
        if not body_without_heading:
            continue
        title = f"{base.title} — {heading}"
        content = f"# {base.title}\n\n{section}"
        identity_heading = _date_heading_identity_text(heading)
        identity_base = (published_at.date().isoformat(), identity_heading)
        occurrence = occurrences.get(identity_base, 0) + 1
        occurrences[identity_base] = occurrence
        raw_version, product_version = extract_version(title, content[:1_000])
        output.append(
            CleanedItem(
                title=title,
                content=content,
                url=base.url,
                publish_time=published_at,
                author=base.author,
                raw_version=raw_version,
                product_version=product_version,
                source_metadata={
                    **base.source_metadata,
                    "date_segmentation": "heading",
                    "section_date": published_at.isoformat(),
                    "section_heading": heading,
                    "identity_key": (
                        f"dated-heading:{published_at.date().isoformat()}:"
                        f"{identity_heading}:{occurrence}"
                    ),
                },
            )
        )
    return output


def _markdown_table(headers: list[str], values: list[str]) -> str:
    width = max(len(headers), len(values))
    normalized_headers = [
        normalize_text(headers[index], preserve_lines=False) if index < len(headers) else ""
        for index in range(width)
    ]
    normalized_values = [
        normalize_text(values[index], preserve_lines=False) if index < len(values) else ""
        for index in range(width)
    ]
    normalized_headers = [value or f"Field {index + 1}" for index, value in enumerate(normalized_headers)]

    def escape(value: str) -> str:
        return value.replace("|", r"\|")

    return "\n".join(
        (
            "| " + " | ".join(escape(value) for value in normalized_headers) + " |",
            "| " + " | ".join("---" for _ in range(width)) + " |",
            "| " + " | ".join(escape(value) for value in normalized_values) + " |",
        )
    )


def _dated_table_items(
    record: RawRecord,
    html: str,
    base: CleanedItem,
    *,
    cutoff: datetime | None,
) -> list[CleanedItem]:
    soup = _prepare_soup(html)
    root: Tag | BeautifulSoup = soup
    configured_selector = str(
        record.source_metadata.get("undated_detail_root_selector") or ""
    ).strip()
    if configured_selector:
        try:
            selected = soup.select_one(configured_selector)
        except Exception:
            selected = None
        if selected is not None:
            root = selected

    occurrences: dict[tuple[str, str], int] = {}
    output: list[CleanedItem] = []
    for table_index, table in enumerate(root.find_all("table")):
        header_row = table.find("tr")
        headers = (
            [_table_cell_text(cell) for cell in header_row.find_all(["th", "td"], recursive=False)]
            if header_row is not None
            else []
        )
        stable_column = next(
            (
                index
                for index, header in enumerate(headers)
                if re.sub(
                    r"[\s_-]+",
                    "",
                    normalize_text(header, preserve_lines=False).casefold(),
                )
                in _STABLE_CHANGELOG_TABLE_HEADERS
            ),
            None,
        )
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            values = [_table_cell_text(cell) for cell in cells]
            published_at = _explicit_changelog_date(" ".join(values))
            if published_at is None or (cutoff is not None and published_at < cutoff):
                continue
            row_label = next(
                (
                    normalize_text(value, preserve_lines=False)[:80]
                    for value in values
                    if value and _explicit_changelog_date(value) is None
                ),
                "",
            )
            stable_label = (
                normalize_text(values[stable_column], preserve_lines=False)[:80].casefold()
                if stable_column is not None and stable_column < len(values)
                else ""
            )
            identity_base = (published_at.date().isoformat(), stable_label)
            occurrence = occurrences.get(identity_base, 0) + 1
            occurrences[identity_base] = occurrence
            date_label = published_at.date().isoformat()
            title = f"{base.title} — {date_label}"
            if row_label:
                title += f" — {row_label}"
            content = f"# {base.title}\n\n## {date_label}\n\n{_markdown_table(headers, values)}"
            raw_version, product_version = extract_version(title, content[:1_000])
            output.append(
                CleanedItem(
                    title=title,
                    content=content,
                    url=base.url,
                    publish_time=published_at,
                    author=base.author,
                    raw_version=raw_version,
                    product_version=product_version,
                    source_metadata={
                        **base.source_metadata,
                        "date_segmentation": "table_row",
                        "section_date": published_at.isoformat(),
                        "section_heading": row_label or date_label,
                        "table_index": table_index,
                        "identity_basis": "stable_column" if stable_label else "date_ordinal",
                        "identity_key": (
                            f"dated-row:{date_label}:{stable_label}:{occurrence}"
                        ),
                    },
                )
            )
    return output


def _configured_directory_changelog_items(
    record: RawRecord,
    html: str,
) -> list[CleanedItem]:
    """Split one configured aggregate log page into cutoff-bounded entries."""

    base_url = record.canonical_url or record.requested_url
    base = _html_metadata(html, base_url, record.source_type)
    configured_selector = str(
        record.source_metadata.get("undated_detail_root_selector") or ""
    ).strip()
    content = clean_html(
        html,
        prefer_article=True,
        deduplicate_blocks=False,
        root_selector=configured_selector or None,
    )
    if base.title and not content.startswith(f"# {base.title}"):
        content = f"# {base.title}\n\n{content}" if content else f"# {base.title}"
    base.content = _strip_changelog_page_lead(content)
    # A publisher-controlled canonical tag must not escape the directory scope
    # already validated by the collector after redirects.
    base.url = normalize_url(base_url)
    cutoff = normalize_datetime(record.source_metadata.get("cutoff"))
    heading_items = _dated_heading_items(base, cutoff=cutoff)
    if heading_items:
        return heading_items
    return _dated_table_items(record, html, base, cutoff=cutoff)


def _github_item(record: RawRecord, value: Any) -> CleanedItem:
    envelope = value if isinstance(value, dict) else {}
    item = envelope.get("item") if isinstance(envelope.get("item"), dict) else envelope
    kind = envelope.get("kind") or record.source_type.value
    repository = envelope.get("repository") or record.source_metadata.get("repository")
    metadata: dict[str, Any] = {"github_kind": kind}
    if repository:
        metadata["repository"] = repository

    if kind == "github_release" or record.source_type == SourceType.GITHUB_RELEASE:
        title = item.get("name") or item.get("tag_name") or "Untitled GitHub release"
        body = _clean_rich_text(item.get("body") or "")
        state = []
        if item.get("draft"):
            state.append("draft")
        if item.get("prerelease"):
            state.append("prerelease")
        content_parts = [title, body]
        if state:
            content_parts.append(f"Release state: {', '.join(state)}")
        author_data = item.get("author") if isinstance(item.get("author"), dict) else {}
        author = author_data.get("login")
        raw_version = item.get("tag_name")
        _, product_version = extract_version(raw_version, title)
        return CleanedItem(
            title=normalize_text(title, preserve_lines=False),
            content=_deduplicate_lines("\n\n".join(filter(None, content_parts))),
            url=normalize_url(item.get("html_url") or record.canonical_url or record.requested_url),
            publish_time=normalize_datetime(item.get("published_at") or item.get("created_at") or record.published_at),
            author=author,
            raw_version=raw_version,
            product_version=product_version,
            source_metadata=metadata,
        )

    title = item.get("title") or "Untitled GitHub issue"
    labels = item.get("labels") if isinstance(item.get("labels"), list) else []
    label_names = [label.get("name") if isinstance(label, dict) else str(label) for label in labels]
    content_parts = [title, _clean_rich_text(item.get("body") or "")]
    if item.get("state"):
        content_parts.append(f"Issue state: {item['state']}")
    if label_names:
        content_parts.append(f"Labels: {', '.join(filter(None, label_names))}")
    comments = envelope.get("comments") if isinstance(envelope.get("comments"), list) else []
    for comment in comments:
        if not isinstance(comment, dict) or not comment.get("body"):
            continue
        user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
        heading = f"Comment by {user.get('login', 'unknown')}"
        if comment.get("created_at"):
            heading += f" at {comment['created_at']}"
        content_parts.extend((heading, _clean_rich_text(comment["body"])))
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    try:
        declared_comment_count = int(item.get("comments", len(comments)))
    except (TypeError, ValueError):
        declared_comment_count = len(comments)
    metadata.update(
        {
            "issue_number": item.get("number"),
            "issue_state": item.get("state"),
            "labels": list(filter(None, label_names)),
            "comment_count": len(comments),
            "declared_comment_count": declared_comment_count,
            "comments_truncated": declared_comment_count > len(comments),
        }
    )
    return CleanedItem(
        title=normalize_text(title, preserve_lines=False),
        content=_deduplicate_lines("\n\n".join(filter(None, content_parts))),
        url=normalize_url(item.get("html_url") or record.canonical_url or record.requested_url),
        publish_time=normalize_datetime(item.get("created_at") or record.published_at),
        author=user.get("login"),
        source_metadata=metadata,
    )


def _changelog_json_item(record: RawRecord, envelope: dict[str, Any]) -> CleanedItem:
    entry = envelope.get("entry") if isinstance(envelope.get("entry"), dict) else envelope
    title = normalize_text(entry.get("title") or "Untitled changelog entry", preserve_lines=False)
    raw_content = entry.get("content")
    if isinstance(raw_content, list):
        content_parts = [entry.get("summary") or "", *[str(item) for item in raw_content]]
    else:
        content_parts = [entry.get("summary") or "", raw_content or ""]
    cleaned_parts = [
        clean_html(part) if "<" in part and ">" in part else normalize_text(part)
        for part in content_parts
        if part
    ]
    content = _deduplicate_lines("\n\n".join(cleaned_parts)) or title
    link = entry.get("link") or record.canonical_url or record.requested_url
    raw_version, product_version = extract_version(title, content[:500])
    return CleanedItem(
        title=title,
        content=content,
        url=normalize_url(link),
        publish_time=normalize_datetime(
            entry.get("published_at") or entry.get("updated_at") or record.published_at
        ),
        author=normalize_text(entry.get("author"), preserve_lines=False) if entry.get("author") else None,
        raw_version=raw_version,
        product_version=product_version,
        source_metadata={
            "feed_url": envelope.get("feed_url"),
            "feed_entry_id": entry.get("id"),
            "feed_tags": entry.get("tags") or [],
        },
    )


def _json_path(value: Any, path: str | None) -> Any:
    current = value
    if not path:
        return current
    for part in str(path).split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _configured_json_document_item(
    record: RawRecord,
    value: Any,
    configuration: Mapping[str, Any],
) -> CleanedItem | None:
    """Build one document from a configured publisher JSON response."""

    document = _json_path(value, str(configuration.get("result_path") or ""))
    if not isinstance(document, dict):
        return None

    title_value = _json_path(
        document,
        str(configuration.get("title_field") or "title"),
    )
    title = normalize_text(
        title_value or "Untitled JSON document",
        preserve_lines=False,
    )
    raw_fields = configuration.get("content_fields", ["content"])
    if isinstance(raw_fields, str):
        raw_fields = [raw_fields]
    content_parts: list[str] = []
    if isinstance(raw_fields, list):
        for field_name in raw_fields:
            raw_part = _json_path(document, str(field_name))
            if raw_part is None:
                continue
            if isinstance(raw_part, (dict, list)):
                cleaned = clean_json(raw_part)
            else:
                text = str(raw_part)
                cleaned = clean_html(text) if "<" in text and ">" in text else normalize_text(text)
            if cleaned:
                content_parts.append(cleaned)
    content = _deduplicate_lines("\n\n".join(content_parts))
    if not content:
        return None

    publish_time = normalize_datetime(
        _json_path(
            document,
            str(configuration.get("publish_time_field") or ""),
        )
    )
    author_field = configuration.get("author_field")
    author_value = _json_path(document, author_field) if author_field else None
    raw_version, product_version = extract_version(title, content[:1_000])
    return CleanedItem(
        title=title,
        content=content,
        url=normalize_url(record.canonical_url or record.requested_url),
        publish_time=publish_time or record.published_at,
        author=(
            normalize_text(author_value, preserve_lines=False)
            if author_value
            else None
        ),
        raw_version=raw_version,
        product_version=product_version,
        source_metadata={"structured_json_extraction": True},
    )


def extract_items(record: RawRecord, payload: str | bytes | dict[str, Any] | list[Any]) -> list[CleanedItem]:
    """Extract logical documents according to source and content type."""

    content_type = (record.content_type or "").lower()
    raw_path = (record.payload_path or "").lower()
    is_json = "json" in content_type or raw_path.endswith(".json")
    is_feed = any(token in content_type for token in ("rss", "atom", "xml")) or raw_path.endswith((".rss", ".xml"))

    if record.source_type in {SourceType.GITHUB_RELEASE, SourceType.GITHUB_ISSUE} or is_json:
        value = payload
        if isinstance(payload, bytes):
            value = payload.decode("utf-8-sig", errors="replace")
        if isinstance(value, str):
            value = json.loads(value)
        if record.source_type in {SourceType.GITHUB_RELEASE, SourceType.GITHUB_ISSUE}:
            values = value if isinstance(value, list) else [value]
            return [_github_item(record, item) for item in values]
        json_document = record.source_metadata.get("json_document")
        if isinstance(json_document, Mapping):
            configured = _configured_json_document_item(
                record,
                value,
                json_document,
            )
            return [configured] if configured is not None else []
        values = value if isinstance(value, list) else [value]
        items: list[CleanedItem] = []
        for index, item in enumerate(values):
            if (
                record.source_type == SourceType.OFFICIAL_CHANGELOG
                and isinstance(item, dict)
                and item.get("kind") == "changelog_entry"
            ):
                items.append(_changelog_json_item(record, item))
                continue
            title = item.get("title") or item.get("name") if isinstance(item, dict) else None
            content = clean_json(item)
            raw_version, product_version = extract_version(title, content[:500])
            items.append(
                CleanedItem(
                    title=normalize_text(title or f"JSON item {index + 1}", preserve_lines=False),
                    content=content,
                    url=normalize_url(record.canonical_url or record.requested_url),
                    publish_time=record.published_at,
                    raw_version=raw_version,
                    product_version=product_version,
                )
            )
        return items

    if is_feed:
        entries = _rss_entries(payload)
        base_url = record.canonical_url or record.requested_url
        for item in entries:
            item.url = normalize_url(urljoin(base_url, item.url or base_url))
        return entries

    html = _decode_html(payload, content_type) if isinstance(payload, bytes) else str(payload)
    if (
        record.source_type == SourceType.OFFICIAL_CHANGELOG
        and record.source_metadata.get("discovery_mode")
        == "configured_undated_directory"
    ):
        return _configured_directory_changelog_items(record, html)
    embedded_mode = str(record.source_metadata.get("embedded_content") or "")
    if embedded_mode == "trae_router_document":
        embedded_item = _trae_router_document(
            html,
            record.canonical_url or record.requested_url,
        )
        if embedded_item is not None:
            return [embedded_item]
    return [
        _html_metadata(
            html,
            record.canonical_url or record.requested_url,
            record.source_type,
            embedded_mode=embedded_mode or None,
        )
    ]


def read_payload(path: str | Path) -> bytes:
    """Read an immutable raw payload without attempting type conversion."""

    return Path(path).read_bytes()
