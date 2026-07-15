"""Persistent exact deduplication and document validity management."""

from __future__ import annotations

import json
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from schemas.document import IndexStatus, StructuredDocument

from .normalizers import normalize_datetime, stable_id


@dataclass(frozen=True, slots=True)
class VersionMergeResult:
    documents: list[StructuredDocument]
    added: int
    unchanged: int
    superseded: int
    output_path: Path


class VersionStore:
    """JSONL-backed version store with atomic replacement on successful merge."""

    def __init__(self, output_path: str | Path) -> None:
        self.output_path = Path(output_path)

    def load(self) -> list[StructuredDocument]:
        if not self.output_path.exists():
            return []
        documents: list[StructuredDocument] = []
        with self.output_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    documents.append(StructuredDocument.model_validate_json(line))
                except (ValidationError, ValueError) as exc:
                    raise ValueError(
                        f"Invalid structured document at {self.output_path}:{line_number}: {exc}"
                    ) from exc
        return documents

    @staticmethod
    def _merge_seen_metadata(
        existing: StructuredDocument,
        incoming: StructuredDocument,
    ) -> StructuredDocument:
        merged = existing.model_copy(deep=True)
        metadata = dict(existing.source_metadata)
        existing_first_seen = (
            normalize_datetime(metadata.get("first_seen_at")) or existing.crawl_time
        )
        incoming_first_seen = (
            normalize_datetime(incoming.source_metadata.get("first_seen_at"))
            or incoming.crawl_time
        )
        raw_ids = list(metadata.get("raw_record_ids", []))
        for raw_id in (existing.raw_record_id, incoming.raw_record_id):
            if raw_id and raw_id not in raw_ids:
                raw_ids.append(raw_id)
        metadata.update(incoming.source_metadata)
        metadata["raw_record_ids"] = raw_ids
        metadata["first_seen_at"] = min(existing_first_seen, incoming_first_seen).isoformat()
        metadata["last_seen_at"] = max(existing.crawl_time, incoming.crawl_time).isoformat()
        merged.source_metadata = metadata
        merged.raw_record_id = incoming.raw_record_id
        merged.raw_path = incoming.raw_path
        merged.competitor = incoming.competitor
        merged.title = incoming.title
        merged.evidence_level = incoming.evidence_level
        merged.url = incoming.url
        merged.raw_version = incoming.raw_version or merged.raw_version
        merged.product_version = incoming.product_version or merged.product_version
        merged.publish_time = incoming.publish_time or merged.publish_time
        merged.language = incoming.language
        merged.author = incoming.author or merged.author
        if incoming.crawl_time > merged.crawl_time:
            merged.crawl_time = incoming.crawl_time
        merged.needs_review = incoming.needs_review
        merged.label_confidence = incoming.label_confidence
        merged.label_reasons = incoming.label_reasons
        merged.dimension_tags = incoming.dimension_tags
        merged.event_type = incoming.event_type
        return merged

    @staticmethod
    def _first_seen(document: StructuredDocument) -> datetime:
        return (
            normalize_datetime(document.source_metadata.get("first_seen_at"))
            or document.crawl_time
        )

    @classmethod
    def _matching_occurrence(
        cls,
        versions: Iterable[StructuredDocument],
        incoming: StructuredDocument,
    ) -> StructuredDocument | None:
        """Find the content occurrence covering an observation timestamp."""

        observed_at = cls._first_seen(incoming)
        ordered = sorted(versions, key=lambda item: (cls._first_seen(item), item.version_id))
        if (
            ordered
            and observed_at < cls._first_seen(ordered[0])
            and ordered[0].content_hash == incoming.content_hash
        ):
            return ordered[0]
        for index, version in enumerate(ordered):
            if version.content_hash != incoming.content_hash:
                continue
            start = cls._first_seen(version)
            end = (
                cls._first_seen(ordered[index + 1])
                if index + 1 < len(ordered)
                else None
            )
            if observed_at >= start and (end is None or observed_at < end):
                return version
        return None

    @classmethod
    def _recurrence_version(cls, document: StructuredDocument) -> StructuredDocument:
        """Give a repeated content state its own non-contiguous interval identity."""

        recurrence = document.model_copy(deep=True)
        base_version_id = str(
            recurrence.source_metadata.get("base_version_id") or recurrence.version_id
        )
        observed_at = cls._first_seen(recurrence)
        recurrence.source_metadata["base_version_id"] = base_version_id
        recurrence.source_metadata["occurrence_started_at"] = observed_at.isoformat()
        recurrence.version_id = stable_id(
            "ver",
            recurrence.document_id,
            recurrence.content_hash,
            "recurrence",
            observed_at.isoformat(),
        )
        return recurrence

    @classmethod
    def _normalise_intervals(
        cls,
        documents: list[StructuredDocument],
    ) -> tuple[list[StructuredDocument], int]:
        grouped: dict[str, list[StructuredDocument]] = defaultdict(list)
        for document in documents:
            grouped[document.document_id].append(document.model_copy(deep=True))

        superseded = 0
        output: list[StructuredDocument] = []
        for versions in grouped.values():
            versions.sort(
                key=lambda item: (
                    cls._first_seen(item),
                    item.version_id,
                )
            )
            effective_starts: list[datetime] = []
            for index, version in enumerate(versions):
                observed_at = cls._first_seen(version)
                if index == 0:
                    declared_start = version.valid_from or version.publish_time or observed_at
                    effective_starts.append(min(declared_start, observed_at))
                else:
                    # A changed representation becomes valid when that exact
                    # content was first observed.  Reusing the entity's
                    # original publish time would create zero-length history
                    # for mutable Issues and pages.
                    effective_starts.append(observed_at)
            for index, version in enumerate(versions):
                version.valid_from = effective_starts[index]
                was_current = version.is_current
                is_latest = index == len(versions) - 1
                version.is_current = is_latest
                if is_latest:
                    version.valid_to = None
                    if version.index_status == IndexStatus.STALE:
                        version.index_status = IndexStatus.PENDING
                else:
                    boundary = effective_starts[index + 1]
                    version.valid_to = max(boundary, version.valid_from or boundary)
                    if version.index_status in {IndexStatus.PENDING, IndexStatus.INDEXED}:
                        version.index_status = IndexStatus.STALE
                    if was_current:
                        superseded += 1
                output.append(version)

        output.sort(
            key=lambda item: (
                item.competitor.casefold(),
                item.source_type.value,
                item.document_id,
                item.valid_from or datetime.min.replace(tzinfo=timezone.utc),
                item.version_id,
            )
        )
        return output, superseded

    def write(self, documents: Iterable[StructuredDocument]) -> None:
        """Atomically serialise validated documents as UTF-8 JSON Lines."""

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{self.output_path.name}.",
                suffix=".tmp",
                dir=self.output_path.parent,
                delete=False,
            ) as handle:
                temporary_name = handle.name
                for document in documents:
                    payload = document.model_dump(mode="json")
                    handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                    handle.write("\n")
                handle.flush()
            Path(temporary_name).replace(self.output_path)
        finally:
            if temporary_name:
                temporary = Path(temporary_name)
                if temporary.exists():
                    temporary.unlink()

    def merge(
        self,
        incoming: Iterable[StructuredDocument],
        write: bool = True,
        include_existing: bool = True,
    ) -> VersionMergeResult:
        """Merge exact versions and recompute current validity intervals.

        ``include_existing=False`` rebuilds the derived store exclusively from
        the supplied raw-record replay.  This keeps processing-rule corrections
        from being recorded as product content changes.
        """

        existing = self.load() if include_existing else []
        previously_current = {
            item.version_id for item in existing if item.is_current
        }
        versions: dict[str, StructuredDocument] = {item.version_id: item for item in existing}
        added = 0
        unchanged = 0
        candidates = [candidate.model_copy(deep=True) for candidate in incoming]
        candidates.sort(
            key=lambda item: (
                self._first_seen(item),
                item.document_id,
                item.version_id,
                item.raw_record_id,
            )
        )
        for document in candidates:
            entity_versions = [
                version
                for version in versions.values()
                if version.document_id == document.document_id
            ]
            matching_occurrence = self._matching_occurrence(entity_versions, document)
            if matching_occurrence is not None:
                versions[matching_occurrence.version_id] = self._merge_seen_metadata(
                    matching_occurrence,
                    document,
                )
                unchanged += 1
                continue

            if document.version_id in versions:
                document = self._recurrence_version(document)

            previous = versions.get(document.version_id)
            if previous is not None:
                versions[document.version_id] = self._merge_seen_metadata(previous, document)
                unchanged += 1
            else:
                versions[document.version_id] = document
                added += 1

        documents, _ = self._normalise_intervals(list(versions.values()))
        superseded = sum(
            1
            for document in documents
            if document.version_id in previously_current and not document.is_current
        )
        if write:
            self.write(documents)
        return VersionMergeResult(
            documents=documents,
            added=added,
            unchanged=unchanged,
            superseded=superseded,
            output_path=self.output_path,
        )
