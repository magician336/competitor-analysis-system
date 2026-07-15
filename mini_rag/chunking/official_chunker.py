"""Source-aware chunking for official product pages."""

from __future__ import annotations

from typing import Any

from schemas.document import StructuredDocument

from .base_chunker import BaseChunker, TextSection


class OfficialPageChunker(BaseChunker):
    """Preserve official-page feature headings and paragraph boundaries."""

    section_type = "official_content"

    def _section_metadata(
        self,
        document: StructuredDocument,
        section: TextSection,
        content: str,
        start: int,
        end: int,
    ) -> dict[str, Any]:
        metadata = super()._section_metadata(document, section, content, start, end)
        metadata.setdefault("section_type", self.section_type)
        return metadata


__all__ = ["OfficialPageChunker"]
