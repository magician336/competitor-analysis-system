"""Raw metadata/payload to versioned ``documents.jsonl`` pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from schemas.document import EvidenceLevel, RawRecord, SourceType, StructuredDocument

from .cleaners import CleanedItem, extract_items, read_payload
from .labeling import RuleLabeler
from .normalizers import (
    detect_language,
    normalize_datetime,
    normalize_text,
    normalize_url,
    sha256_bytes,
    sha256_text,
    stable_id,
)
from .versioning import VersionMergeResult, VersionStore


@dataclass(frozen=True, slots=True)
class ProcessingFailure:
    meta_path: Path
    error: str
    raw_record_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    scanned: int
    processed_records: int
    generated_documents: int
    skipped: int
    added: int
    unchanged: int
    superseded: int
    output_path: Path
    output_written: bool
    failures: list[ProcessingFailure] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.failures

    @property
    def partial_failure(self) -> bool:
        return bool(self.failures) and self.processed_records > 0


_EVIDENCE_BY_SOURCE: dict[SourceType, EvidenceLevel] = {
    SourceType.OFFICIAL_PAGE: EvidenceLevel.A,
    SourceType.OFFICIAL_CHANGELOG: EvidenceLevel.A,
    SourceType.PRICING: EvidenceLevel.A,
    SourceType.PRODUCT_DOCS: EvidenceLevel.A,
    SourceType.STATUS_PAGE: EvidenceLevel.A,
    SourceType.GITHUB_RELEASE: EvidenceLevel.B,
    SourceType.GITHUB_ISSUE: EvidenceLevel.C,
    SourceType.PLUGIN_MARKETPLACE: EvidenceLevel.B,
    SourceType.COMMUNITY: EvidenceLevel.C,
    SourceType.REVIEW: EvidenceLevel.C,
    SourceType.SECURITY_PRIVACY: EvidenceLevel.A,
    SourceType.BENCHMARK: EvidenceLevel.B,
    SourceType.RSS: EvidenceLevel.B,
}


class ProcessingPipeline:
    """Replayable processing pipeline over immutable raw records."""

    def __init__(
        self,
        project_root: str | Path = ".",
        raw_dir: str | Path = "data/raw",
        output_path: str | Path = "data/cleaned/documents.jsonl",
        labeler: RuleLabeler | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        raw = Path(raw_dir)
        output = Path(output_path)
        self.raw_dir = raw.resolve() if raw.is_absolute() else (self.project_root / raw).resolve()
        self.output_path = output.resolve() if output.is_absolute() else (self.project_root / output).resolve()
        if labeler is not None:
            self.labeler = labeler
        else:
            dimensions_path = self.project_root / "config" / "dimensions.yaml"
            self.labeler = (
                RuleLabeler.from_yaml(dimensions_path)
                if dimensions_path.is_file()
                else RuleLabeler()
            )
        self.version_store = VersionStore(self.output_path)

    def discover_meta_files(self) -> list[Path]:
        """Return raw metadata files in stable path order."""

        if not self.raw_dir.exists():
            return []
        return sorted(self.raw_dir.rglob("*.meta.json"), key=lambda item: item.as_posix())

    def _resolve_payload_path(self, record: RawRecord, meta_path: Path) -> Path | None:
        if not record.payload_path:
            return None
        raw_path = Path(record.payload_path)
        if raw_path.is_absolute():
            return raw_path
        candidates = (
            self.raw_dir / raw_path,
            self.project_root / raw_path,
            meta_path.parent / raw_path,
            meta_path.parent / raw_path.name,
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        return candidates[0].resolve()

    @staticmethod
    def _is_processable(record: RawRecord) -> bool:
        if record.source_metadata.get("record_role") in {
            "changelog_index",
            "changelog_candidate",
            "discovery_sitemap",
            "feed_empty",
        }:
            return False
        if record.error:
            return False
        if record.http_status == 304:
            return False
        if record.http_status is not None and not 200 <= record.http_status < 300:
            return False
        return bool(record.payload_path)

    def _make_document(
        self,
        record: RawRecord,
        item: CleanedItem,
        payload_path: Path,
        item_index: int,
    ) -> StructuredDocument | None:
        title = normalize_text(item.title, preserve_lines=False) or "Untitled document"
        content = normalize_text(item.content)
        if not content:
            return None
        url = normalize_url(item.url or record.canonical_url or record.final_url or record.requested_url)
        # The item index is only used when a multi-item payload lacks stable
        # item URLs.  GitHub and RSS records normally supply one.
        identity_url = url
        identity_key = normalize_text(
            item.source_metadata.get("identity_key"),
            preserve_lines=False,
        )
        if identity_key:
            identity_url = f"{url}\0{identity_key}"
        elif item_index and url == normalize_url(record.canonical_url or record.requested_url):
            identity_url = f"{url}?coderadar_item={item_index}"
        content_hash = sha256_text(content)
        competitor_id = normalize_text(record.competitor, preserve_lines=False)
        competitor_name = normalize_text(
            record.source_metadata.get("competitor_name") or competitor_id,
            preserve_lines=False,
        )
        document_id = stable_id(
            "doc",
            competitor_id.casefold(),
            record.source_type.value,
            identity_url,
        )
        version_id = stable_id("ver", document_id, content_hash)
        labels = self.labeler.label(record.source_type, title, content)
        label_reasons = list(labels.label_reasons)
        if record.needs_browser:
            label_reasons.append("source:needs_browser")
        source_metadata = dict(record.source_metadata)
        source_metadata.update(item.source_metadata)
        source_metadata.update(
            {
                "competitor_id": competitor_id,
                "crawl_run_id": record.crawl_run_id,
                "first_seen_at": record.fetched_at.isoformat(),
                "last_seen_at": record.fetched_at.isoformat(),
                "requested_url": record.requested_url,
                "final_url": record.final_url,
                "payload_hash": record.payload_hash,
                "etag": record.etag,
                "last_modified": record.last_modified,
                "needs_browser": record.needs_browser,
            }
        )
        configured_effective_at = normalize_datetime(
            record.source_metadata.get("event_effective_at")
        )
        publish_time = configured_effective_at or item.publish_time or record.published_at
        if configured_effective_at is not None:
            source_metadata["publish_time_basis"] = "configured_event_effective_at"
        valid_from = (
            publish_time
            if (
                configured_effective_at is not None
                or record.source_type
                in {
                    SourceType.OFFICIAL_CHANGELOG,
                    SourceType.GITHUB_RELEASE,
                    SourceType.RSS,
                }
            )
            and publish_time is not None
            else record.fetched_at
        )
        try:
            relative_raw_path = payload_path.relative_to(self.project_root).as_posix()
        except ValueError:
            relative_raw_path = payload_path.as_posix()
        return StructuredDocument(
            document_id=document_id,
            version_id=version_id,
            raw_record_id=record.raw_record_id,
            raw_path=relative_raw_path,
            competitor=competitor_name,
            title=title,
            content=content,
            source_type=record.source_type,
            evidence_level=self._evidence_level(record),
            url=url,
            raw_version=item.raw_version,
            product_version=item.product_version,
            publish_time=publish_time,
            crawl_time=record.fetched_at,
            event_type=labels.event_type,
            dimension_tags=labels.dimension_tags,
            content_hash=content_hash,
            valid_from=valid_from,
            language=detect_language(f"{title}\n{content}"),
            author=item.author,
            source_metadata=source_metadata,
            label_confidence=labels.label_confidence,
            label_reasons=label_reasons,
            needs_review=labels.needs_review or record.needs_browser,
        )

    @staticmethod
    def _evidence_level(record: RawRecord) -> EvidenceLevel:
        configured = record.source_metadata.get("evidence_level")
        if configured is not None:
            try:
                return (
                    configured
                    if isinstance(configured, EvidenceLevel)
                    else EvidenceLevel(str(configured).strip().upper())
                )
            except ValueError:
                pass
        return _EVIDENCE_BY_SOURCE[record.source_type]

    def process_record(self, record: RawRecord, payload_path: str | Path) -> list[StructuredDocument]:
        """Process one validated raw record without writing the version store."""

        path = Path(payload_path)
        payload = read_payload(path)
        if record.payload_hash and sha256_bytes(payload) != record.payload_hash:
            raise ValueError(
                f"Payload SHA-256 mismatch for raw record {record.raw_record_id}"
            )
        items = extract_items(record, payload)
        output: list[StructuredDocument] = []
        for index, item in enumerate(items):
            document = self._make_document(record, item, path, index)
            if document is not None:
                output.append(document)
        return output

    def process(
        self,
        meta_paths: Iterable[str | Path] | None = None,
        force: bool = False,
        rebuild: bool = False,
    ) -> ProcessingResult:
        """Process metadata files independently and merge successful documents."""

        if meta_paths is None:
            paths = self.discover_meta_files()
        else:
            paths = []
            for item in meta_paths:
                path = Path(item)
                paths.append(path if path.is_absolute() else self.project_root / path)
        paths = sorted((item.resolve() for item in paths), key=lambda item: item.as_posix())
        incoming: list[StructuredDocument] = []
        failures: list[ProcessingFailure] = []
        skipped = 0
        processed_records = 0

        for meta_path in paths:
            raw_record_id: str | None = None
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8-sig"))
                record = RawRecord.model_validate(metadata)
                raw_record_id = record.raw_record_id
                if not self._is_processable(record):
                    skipped += 1
                    expected_skip = (
                        record.source_metadata.get("record_role")
                        in {
                            "changelog_index",
                            "changelog_candidate",
                            "discovery_sitemap",
                            "feed_empty",
                        }
                        or record.http_status == 304
                    )
                    acquisition_failure = bool(record.error) or (
                        record.http_status is not None
                        and record.http_status != 304
                        and not 200 <= record.http_status < 300
                    )
                    # Acquisition failures remain auditable in immutable raw
                    # metadata. They are not transformation failures and must
                    # not make every later replay fail permanently.
                    if not expected_skip and not acquisition_failure:
                        failures.append(
                            ProcessingFailure(
                                meta_path=meta_path,
                                raw_record_id=raw_record_id,
                                error="Successful raw record has no payload path",
                            )
                        )
                    continue
                payload_path = self._resolve_payload_path(record, meta_path)
                if payload_path is None or not payload_path.is_file():
                    raise FileNotFoundError(
                        f"Payload not found for raw record {record.raw_record_id}: {payload_path}"
                    )
                documents = self.process_record(record, payload_path)
                if not documents:
                    skipped += 1
                    if not record.needs_browser:
                        failures.append(
                            ProcessingFailure(
                                meta_path=meta_path,
                                raw_record_id=raw_record_id,
                                error="Payload produced no non-empty document",
                            )
                        )
                    continue
                incoming.extend(documents)
                processed_records += 1
            except (OSError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
                failures.append(
                    ProcessingFailure(
                        meta_path=meta_path,
                        raw_record_id=raw_record_id,
                        error=str(exc),
                    )
                )

        # Reprocessing is inherently safe because stable version IDs make the
        # merge idempotent.  ``force`` is accepted by the shared CLI contract
        # and intentionally does not discard existing history.
        _ = force
        output_written = not (rebuild and failures)
        merged: VersionMergeResult = self.version_store.merge(
            incoming,
            write=output_written,
            include_existing=not rebuild,
        )
        return ProcessingResult(
            scanned=len(paths),
            processed_records=processed_records,
            generated_documents=len(incoming),
            skipped=skipped,
            added=merged.added,
            unchanged=merged.unchanged,
            superseded=merged.superseded,
            output_path=merged.output_path,
            output_written=output_written,
            failures=failures,
        )


def process_raw_records(
    project_root: str | Path = ".",
    meta_paths: Iterable[str | Path] | None = None,
    raw_dir: str | Path = "data/raw",
    output_path: str | Path = "data/cleaned/documents.jsonl",
    labeler: RuleLabeler | None = None,
    rebuild: bool = False,
) -> ProcessingResult:
    """Convenience entry point used by scripts and programmatic callers."""

    return ProcessingPipeline(
        project_root=project_root,
        raw_dir=raw_dir,
        output_path=output_path,
        labeler=labeler,
    ).process(meta_paths=meta_paths, rebuild=rebuild)
