"""Version- and category-aware chunking for changelog entries."""

from __future__ import annotations

import re
from typing import Any

from schemas.document import StructuredDocument

from .base_chunker import BaseChunker, TextSection


_VERSION_RE = re.compile(
    r"(?i)(?:\b(?:version|ver(?:sion)?|release)\s*)?\bv?"
    r"(?P<version>\d+\.\d+(?:\.\d+)?(?:[-+][0-9a-z.-]+)?)\b"
)

_PLAIN_VERSION_LINE_RE = re.compile(
    r"(?im)^(?P<label>(?:(?:version|ver(?:sion)?|release)\s+v?"
    r"(?P<named>\d+\.\d+(?:\.\d+)?(?:[-+][0-9a-z.-]+)?)|"
    r"v(?P<prefixed>\d+\.\d+(?:\.\d+)?(?:[-+][0-9a-z.-]+)?)))[ \t]*$"
)

_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("security", ("security", "vulnerability", "cve")),
    ("breaking_changes", ("breaking", "migration", "deprecated", "deprecation")),
    ("known_issues", ("known issue", "limitation")),
    ("fixes", ("fix", "fixed", "bug", "resolved", "patch")),
    ("improvements", ("improvement", "improved", "enhancement", "performance")),
    ("features", ("feature", "new", "added", "introducing", "launch")),
)


class ChangelogChunker(BaseChunker):
    """Keep release versions and change categories in separate chunks."""

    section_type = "changelog_entry"

    def _sections(self, document: StructuredDocument) -> list[TextSection]:
        """Also recognise plain ``Version 1.2`` lines as release boundaries."""

        sections = super()._sections(document)
        version_lines = list(_PLAIN_VERSION_LINE_RE.finditer(document.content))
        if not version_lines:
            return sections

        result: list[TextSection] = []
        for section in sections:
            internal = [
                match
                for match in version_lines
                if section.start <= match.start() < section.end
            ]
            cut_points = sorted(
                {section.start, section.end, *[match.start() for match in internal]}
            )
            for index in range(len(cut_points) - 1):
                start, end = cut_points[index], cut_points[index + 1]
                if not document.content[start:end].strip():
                    continue
                preceding = next(
                    (
                        match
                        for match in reversed(version_lines)
                        if match.start() <= start
                    ),
                    None,
                )
                heading_path = list(section.heading_path)
                metadata = dict(section.metadata)
                if preceding is not None:
                    version = preceding.group("named") or preceding.group("prefixed")
                    metadata["detected_product_version"] = version
                    if not any(_VERSION_RE.search(value) for value in heading_path):
                        heading_path.append(preceding.group("label").strip())
                result.append(
                    TextSection(
                        start=start,
                        end=end,
                        heading_path=heading_path,
                        heading_level=section.heading_level,
                        metadata=metadata,
                    )
                )
        return result

    def _section_metadata(
        self,
        document: StructuredDocument,
        section: TextSection,
        content: str,
        start: int,
        end: int,
    ) -> dict[str, Any]:
        metadata = super()._section_metadata(document, section, content, start, end)
        context = " ".join(section.heading_path).casefold()
        category = "release_notes"
        for name, terms in _CATEGORY_RULES:
            if any(term in context for term in terms):
                category = name
                break

        product_version = metadata.pop(
            "detected_product_version", document.product_version
        )
        for heading in reversed(section.heading_path):
            match = _VERSION_RE.search(heading)
            if match:
                product_version = match.group("version")
                break

        metadata.update(
            {
                "section_type": self.section_type,
                "change_category": category,
                "product_version": product_version,
            }
        )
        return metadata


__all__ = ["ChangelogChunker"]
