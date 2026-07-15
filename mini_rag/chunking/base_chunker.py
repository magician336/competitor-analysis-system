"""Deterministic, source-locatable document chunking primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from schemas.document import StructuredDocument

from mini_rag.models import Chunk


_HEADING_RE = re.compile(
    r"(?m)^(?P<marks>#{1,6})[ \t]+(?P<title>[^\r\n]+?)[ \t]*(?:\r?\n|$)"
)
_HEADING_ONLY_RE = re.compile(r"\A#{1,6}[ \t]+[^\r\n]+[ \t]*\Z")


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Character-based limits used after semantic structure boundaries."""

    target_characters: int = 1200
    maximum_characters: int = 1800
    overlap_characters: int = 160
    minimum_characters: int = 80

    def __post_init__(self) -> None:
        if self.minimum_characters < 1:
            raise ValueError("minimum_characters must be positive")
        if self.target_characters < 1:
            raise ValueError("target_characters must be positive")
        if self.maximum_characters < self.target_characters:
            raise ValueError(
                "maximum_characters must be greater than or equal to target_characters"
            )
        if not 0 <= self.overlap_characters < self.maximum_characters:
            raise ValueError(
                "overlap_characters must be non-negative and smaller than maximum_characters"
            )

    @classmethod
    def from_settings(cls, value: Any | None) -> "ChunkingConfig":
        """Accept this config, a mapping, or ``MiniRAGSettings.chunking``."""

        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if hasattr(value, "chunking"):
            value = value.chunking
        if isinstance(value, dict):
            source = value
            getter = source.get
        else:
            source = value
            getter = lambda name, default: getattr(source, name, default)
        return cls(
            target_characters=int(
                getter("target_characters", getter("target_chars", 1200))
            ),
            maximum_characters=int(
                getter("maximum_characters", getter("max_chars", 1800))
            ),
            overlap_characters=int(
                getter("overlap_characters", getter("overlap_chars", 160))
            ),
            minimum_characters=int(
                getter("minimum_characters", getter("min_chars", 80))
            ),
        )

    @property
    def target_chars(self) -> int:
        return self.target_characters

    @property
    def max_chars(self) -> int:
        return self.maximum_characters

    @property
    def overlap_chars(self) -> int:
        return self.overlap_characters

    @property
    def min_chars(self) -> int:
        return self.minimum_characters


@dataclass(slots=True)
class TextSection:
    """An exact half-open source range and its semantic heading context."""

    start: int
    end: int
    heading_path: list[str]
    heading_level: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseChunker:
    """Generic heading-aware chunker used directly and by source specialisations."""

    section_type = "content"

    def __init__(
        self,
        config: ChunkingConfig | Any | None = None,
        *,
        target_characters: int | None = None,
        maximum_characters: int | None = None,
        overlap_characters: int | None = None,
        minimum_characters: int | None = None,
        target_chars: int | None = None,
        max_chars: int | None = None,
        overlap_chars: int | None = None,
        min_chars: int | None = None,
    ) -> None:
        base = ChunkingConfig.from_settings(config)
        self.config = ChunkingConfig(
            target_characters=(
                target_characters
                if target_characters is not None
                else target_chars
                if target_chars is not None
                else base.target_characters
            ),
            maximum_characters=(
                maximum_characters
                if maximum_characters is not None
                else max_chars
                if max_chars is not None
                else base.maximum_characters
            ),
            overlap_characters=(
                overlap_characters
                if overlap_characters is not None
                else overlap_chars
                if overlap_chars is not None
                else base.overlap_characters
            ),
            minimum_characters=(
                minimum_characters
                if minimum_characters is not None
                else min_chars
                if min_chars is not None
                else base.minimum_characters
            ),
        )

    def chunk(self, document: StructuredDocument) -> list[Chunk]:
        """Split one validated document into deterministic exact source slices."""

        if not isinstance(document, StructuredDocument):
            document = StructuredDocument.model_validate(document)
        chunks: list[Chunk] = []
        for section in self._sections(document):
            for start, end in self._piece_ranges(document.content, section):
                start, end = self._trim_range(document.content, start, end)
                if end <= start:
                    continue
                content = document.content[start:end]
                if _HEADING_ONLY_RE.fullmatch(content):
                    continue
                metadata = self._section_metadata(
                    document, section, content, start, end
                )
                section_type = str(metadata.pop("section_type", self.section_type))
                chunk_data: dict[str, Any] = {
                    "document_id": document.document_id,
                    "version_id": document.version_id,
                    "raw_record_id": document.raw_record_id,
                    "raw_path": document.raw_path,
                    "chunk_index": len(chunks),
                    "title": document.title,
                    "content": content,
                    "heading_path": section.heading_path,
                    "char_start": start,
                    "char_end": end,
                    "section_type": section_type,
                    "competitor": document.competitor,
                    "source_type": document.source_type,
                    "evidence_level": document.evidence_level,
                    "url": document.url,
                    "raw_version": document.raw_version,
                    "product_version": document.product_version,
                    "publish_time": document.publish_time,
                    "crawl_time": document.crawl_time,
                    "valid_from": document.valid_from,
                    "valid_to": document.valid_to,
                    "is_current": document.is_current,
                    "event_type": document.event_type,
                    "dimension_tags": document.dimension_tags,
                    "language": document.language,
                    "author": document.author,
                    "content_hash": document.content_hash,
                    "source_metadata": dict(document.source_metadata),
                }
                chunk_data.update(metadata)
                chunks.append(Chunk(**chunk_data))
        return chunks

    def chunk_many(
        self, documents: Iterable[StructuredDocument]
    ) -> list[Chunk]:
        return [chunk for document in documents for chunk in self.chunk(document)]

    def _sections(self, document: StructuredDocument) -> list[TextSection]:
        """Build source ranges from Markdown headings while retaining hierarchy."""

        content = document.content
        matches = list(_HEADING_RE.finditer(content))
        if not matches:
            return [
                TextSection(
                    start=0,
                    end=len(content),
                    heading_path=[document.title],
                )
            ]

        sections: list[TextSection] = []
        first_start = matches[0].start()
        if content[:first_start].strip():
            sections.append(
                TextSection(
                    start=0,
                    end=first_start,
                    heading_path=[document.title],
                )
            )

        heading_stack: list[str] = []
        for index, match in enumerate(matches):
            level = len(match.group("marks"))
            title = match.group("title").strip().rstrip("#").strip()
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(title)
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            sections.append(
                TextSection(
                    start=match.start(),
                    end=end,
                    heading_path=list(heading_stack),
                    heading_level=level,
                )
            )
        return sections

    def _section_metadata(
        self,
        document: StructuredDocument,
        section: TextSection,
        content: str,
        start: int,
        end: int,
    ) -> dict[str, Any]:
        return dict(section.metadata)

    def _piece_ranges(
        self, content: str, section: TextSection
    ) -> list[tuple[int, int]]:
        start, end = self._trim_range(content, section.start, section.end)
        if end <= start:
            return []
        if end - start <= self.config.maximum_characters:
            return [(start, end)]

        ranges: list[tuple[int, int]] = []
        cursor = start
        while cursor < end:
            remaining = end - cursor
            if remaining <= self.config.maximum_characters:
                ranges.append((cursor, end))
                break
            target = min(cursor + self.config.target_characters, end)
            hard_end = min(cursor + self.config.maximum_characters, end)
            cut = self._best_break(content, cursor, target, hard_end)
            if cut <= cursor:
                cut = hard_end
            ranges.append((cursor, cut))

            next_cursor = max(cursor + 1, cut - self.config.overlap_characters)
            next_cursor = self._align_start(content, next_cursor, cut)
            if next_cursor <= cursor:
                next_cursor = cut
            cursor = next_cursor
        return ranges

    def _best_break(
        self, content: str, start: int, target: int, hard_end: int
    ) -> int:
        minimum = min(
            hard_end,
            start + max(self.config.minimum_characters, self.config.target_characters // 2),
        )
        segment = content[minimum:hard_end]
        candidates: list[tuple[int, int]] = []
        patterns = (
            (r"\r?\n\s*\r?\n", 0),
            (r"\r?\n", 1),
            (r"(?<=[.!?。！？;；])\s+", 2),
            (r"\s+", 3),
        )
        for pattern, priority in patterns:
            for match in re.finditer(pattern, segment):
                position = minimum + match.end()
                candidates.append((position, priority))
        if not candidates:
            return hard_end
        position, _ = min(
            candidates,
            key=lambda item: (abs(item[0] - target), item[1], -item[0]),
        )
        return position

    @staticmethod
    def _align_start(content: str, proposed: int, upper: int) -> int:
        if proposed <= 0 or proposed >= len(content) or content[proposed - 1].isspace():
            return proposed
        while proposed < upper and not content[proposed].isspace():
            proposed += 1
        while proposed < upper and content[proposed].isspace():
            proposed += 1
        return proposed

    @staticmethod
    def _trim_range(content: str, start: int, end: int) -> tuple[int, int]:
        while start < end and content[start].isspace():
            start += 1
        while end > start and content[end - 1].isspace():
            end -= 1
        return start, end


__all__ = ["BaseChunker", "ChunkingConfig", "TextSection"]
