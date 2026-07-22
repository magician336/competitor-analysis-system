"""Source-type dispatch for Mini-RAG chunkers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from schemas.document import SourceType, StructuredDocument

from mini_rag.models import Chunk

from .base_chunker import BaseChunker, ChunkingConfig
from .changelog_chunker import ChangelogChunker
from .github_chunker import GitHubChunker
from .official_chunker import OfficialPageChunker
from .pricing_chunker import PricingChunker


_SOURCE_ALIASES = {
    "official": SourceType.OFFICIAL_PAGE,
    "changelog": SourceType.OFFICIAL_CHANGELOG,
    "pricing": SourceType.PRICING,
    "docs": SourceType.PRODUCT_DOCS,
    "documentation": SourceType.PRODUCT_DOCS,
    "status": SourceType.STATUS_PAGE,
    "release": SourceType.GITHUB_RELEASE,
    "issue": SourceType.GITHUB_ISSUE,
    "marketplace": SourceType.PLUGIN_MARKETPLACE,
    "forum": SourceType.COMMUNITY,
    "reviews": SourceType.REVIEW,
    "security": SourceType.SECURITY_PRIVACY,
    "privacy": SourceType.SECURITY_PRIVACY,
    "trust_center": SourceType.SECURITY_PRIVACY,
    "benchmarks": SourceType.BENCHMARK,
}


class ChunkingDispatcher:
    """Route each StructuredDocument to its source-aware chunker."""

    def __init__(self, config: ChunkingConfig | Any | None = None) -> None:
        self.config = ChunkingConfig.from_settings(config)
        self._chunkers: dict[SourceType, BaseChunker] = {
            SourceType.OFFICIAL_PAGE: OfficialPageChunker(self.config),
            SourceType.OFFICIAL_CHANGELOG: ChangelogChunker(self.config),
            SourceType.PRICING: PricingChunker(self.config),
            SourceType.PRODUCT_DOCS: OfficialPageChunker(self.config),
            SourceType.STATUS_PAGE: OfficialPageChunker(self.config),
            SourceType.GITHUB_RELEASE: GitHubChunker(self.config),
            SourceType.GITHUB_ISSUE: GitHubChunker(self.config),
            SourceType.PLUGIN_MARKETPLACE: OfficialPageChunker(self.config),
            SourceType.COMMUNITY: OfficialPageChunker(self.config),
            SourceType.REVIEW: OfficialPageChunker(self.config),
            SourceType.SECURITY_PRIVACY: OfficialPageChunker(self.config),
            SourceType.BENCHMARK: OfficialPageChunker(self.config),
            SourceType.RSS: ChangelogChunker(self.config),
        }

    def get_chunker(self, source_type: SourceType | str) -> BaseChunker:
        if not isinstance(source_type, SourceType):
            value = str(source_type).strip().lower()
            source_type = (
                _SOURCE_ALIASES[value]
                if value in _SOURCE_ALIASES
                else SourceType(value)
            )
        return self._chunkers[source_type]

    def register(self, source_type: SourceType, chunker: BaseChunker) -> None:
        """Register an explicit chunker, primarily for controlled extensions."""

        self._chunkers[source_type] = chunker

    def chunk(self, document: StructuredDocument) -> list[Chunk]:
        if not isinstance(document, StructuredDocument):
            document = StructuredDocument.model_validate(document)
        return self.get_chunker(document.source_type).chunk(document)

    def chunk_documents(
        self, documents: Iterable[StructuredDocument]
    ) -> list[Chunk]:
        return [chunk for document in documents for chunk in self.chunk(document)]


def get_chunker(
    source_type: SourceType | str,
    config: ChunkingConfig | Any | None = None,
) -> BaseChunker:
    return ChunkingDispatcher(config).get_chunker(source_type)


def chunk_document(
    document: StructuredDocument,
    config: ChunkingConfig | Any | None = None,
) -> list[Chunk]:
    return ChunkingDispatcher(config).chunk(document)


__all__ = ["ChunkingDispatcher", "chunk_document", "get_chunker"]
