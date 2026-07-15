"""RSS and Atom parsing used by the changelog collector."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from time import struct_time
from typing import Any

import feedparser
from dateutil import parser as date_parser


def _as_utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, struct_time):
        parsed = datetime(*value[:6], tzinfo=timezone.utc)
    else:
        try:
            parsed = date_parser.parse(str(value))
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True)
class RSSItem:
    id: str
    title: str
    link: str
    published_at: datetime | None = None
    updated_at: datetime | None = None
    author: str | None = None
    summary: str = ""
    content: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in ("published_at", "updated_at"):
            value = result[name]
            result[name] = value.isoformat().replace("+00:00", "Z") if value else None
        return result


class RSSLoader:
    """Convert RSS/Atom bytes into a stable, JSON-serializable entry shape."""

    def parse(self, payload: bytes | str, source_url: str = "") -> list[RSSItem]:
        parsed = feedparser.parse(payload)
        items: list[RSSItem] = []
        for entry in parsed.entries:
            link = str(entry.get("link") or source_url)
            entry_id = str(entry.get("id") or entry.get("guid") or link)
            content = [
                str(part.get("value", ""))
                for part in entry.get("content", [])
                if isinstance(part, dict) and part.get("value")
            ]
            tags = [
                str(tag.get("term"))
                for tag in entry.get("tags", [])
                if isinstance(tag, dict) and tag.get("term")
            ]
            published = _as_utc(
                entry.get("published_parsed") or entry.get("published")
            )
            updated = _as_utc(entry.get("updated_parsed") or entry.get("updated"))
            items.append(
                RSSItem(
                    id=entry_id,
                    title=str(entry.get("title") or ""),
                    link=link,
                    published_at=published,
                    updated_at=updated,
                    author=str(entry.get("author")) if entry.get("author") else None,
                    summary=str(entry.get("summary") or entry.get("description") or ""),
                    content=content,
                    tags=tags,
                )
            )
        return items


def load_rss(payload: bytes | str, source_url: str = "") -> list[RSSItem]:
    return RSSLoader().parse(payload, source_url)


__all__ = ["RSSItem", "RSSLoader", "load_rss"]
