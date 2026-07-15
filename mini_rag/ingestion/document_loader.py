"""Strict JSON Lines loader for the structured-document boundary."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from pydantic import ValidationError

from schemas.document import StructuredDocument


class DocumentLoadError(ValueError):
    """Raised when a JSON Lines record violates the public document schema."""


@dataclass(frozen=True, slots=True)
class DocumentStatistics:
    total: int
    current: int
    historical: int
    competitors: dict[str, int]
    sources: dict[str, int]
    languages: dict[str, int]
    missing_publish_time: int
    missing_product_version: int
    needs_review: int


class DocumentLoader:
    """Load and audit the immutable ``documents.jsonl`` hand-off."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def iter_documents(self) -> Iterator[StructuredDocument]:
        if not self.path.is_file():
            raise FileNotFoundError(f"Structured document file does not exist: {self.path}")
        seen_versions: set[str] = set()
        current_by_document: dict[str, str] = {}
        with self.path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    document = StructuredDocument.model_validate_json(line)
                except (ValidationError, ValueError) as exc:
                    raise DocumentLoadError(
                        f"Invalid structured document at {self.path}:{line_number}: {exc}"
                    ) from exc
                if document.version_id in seen_versions:
                    raise DocumentLoadError(
                        f"Duplicate version_id at {self.path}:{line_number}: {document.version_id}"
                    )
                seen_versions.add(document.version_id)
                if document.is_current:
                    previous = current_by_document.get(document.document_id)
                    if previous is not None:
                        raise DocumentLoadError(
                            "Multiple current versions for document_id "
                            f"{document.document_id}: {previous}, {document.version_id}"
                        )
                    current_by_document[document.document_id] = document.version_id
                yield document

    def load(self) -> list[StructuredDocument]:
        return list(self.iter_documents())

    @staticmethod
    def statistics(documents: Iterable[StructuredDocument]) -> DocumentStatistics:
        rows = list(documents)
        competitor_counts = Counter(item.competitor for item in rows)
        source_counts = Counter(item.source_type.value for item in rows)
        language_counts = Counter(item.language for item in rows)
        current = sum(item.is_current for item in rows)
        return DocumentStatistics(
            total=len(rows),
            current=current,
            historical=len(rows) - current,
            competitors=dict(sorted(competitor_counts.items())),
            sources=dict(sorted(source_counts.items())),
            languages=dict(sorted(language_counts.items())),
            missing_publish_time=sum(item.publish_time is None for item in rows),
            missing_product_version=sum(item.product_version is None for item in rows),
            needs_review=sum(item.needs_review for item in rows),
        )

