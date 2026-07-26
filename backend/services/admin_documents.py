"""Safe persistence and indexing support for the single-page admin console."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from threading import RLock
import uuid
from typing import Any

from pydantic import ValidationError

from mini_rag.ingestion import DocumentLoader
from schemas.document import (
    DimensionTag,
    EvidenceLevel,
    IndexStatus,
    SourceType,
    StructuredDocument,
)


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = frozenset({".md", ".txt", ".jsonl"})
_DOCUMENT_WRITE_LOCK = RLock()


class AdminImportError(ValueError):
    """An uploaded document cannot safely cross the persistence boundary."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_filename(filename: str) -> str:
    basename = Path(filename or "upload").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    return cleaned[:120] or "upload"


def _serialise_document(document: StructuredDocument) -> str:
    return document.model_dump_json(exclude_none=False)


@contextmanager
def _process_file_lock(path: Path) -> Iterator[None]:
    """Hold an OS-level exclusive lock in addition to the in-process lock."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with _DOCUMENT_WRITE_LOCK, path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _archive_raw_upload(
    *,
    project_root: Path,
    filename: str,
    content: bytes,
) -> Path:
    now = _utc_now()
    directory = project_root / "data" / "raw" / "manual_upload" / now.strftime("%Y%m%d")
    unique_name = (
        f"{now.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:8]}_"
        f"{_safe_filename(filename)}"
    )
    destination = directory / unique_name
    _atomic_write(destination, content)
    return destination


def _relative_or_absolute(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def parse_uploaded_documents(
    *,
    filename: str,
    content: bytes,
    project_root: Path,
    competitor: str | None = None,
    title: str | None = None,
    source_url: str | None = None,
    publish_time: str | None = None,
    dimension_tags: list[str] | None = None,
) -> tuple[list[StructuredDocument], Path]:
    """Validate an upload completely and archive its immutable raw bytes."""

    extension = Path(filename).suffix.casefold()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise AdminImportError("Only .md, .txt, and .jsonl files are supported.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise AdminImportError("The uploaded file exceeds the 5 MB limit.")
    if not content:
        raise AdminImportError("The uploaded file is empty.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AdminImportError("The uploaded file must use UTF-8 encoding.") from exc

    documents: list[StructuredDocument] = []
    if extension == ".jsonl":
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                documents.append(StructuredDocument.model_validate_json(line))
            except (ValidationError, ValueError) as exc:
                raise AdminImportError(
                    f"Invalid StructuredDocument at JSONL line {line_number}: {exc}"
                ) from exc
        if not documents:
            raise AdminImportError("The JSONL upload contains no document records.")
    else:
        resolved_competitor = (competitor or "").strip()
        resolved_title = (title or "").strip()
        resolved_url = (source_url or "").strip()
        if not resolved_competitor:
            raise AdminImportError("competitor is required for Markdown and text uploads.")
        if not resolved_title:
            raise AdminImportError("title is required for Markdown and text uploads.")
        if not text.strip():
            raise AdminImportError("The uploaded document contains no text.")
        if not resolved_url:
            competitor_segment = _safe_filename(resolved_competitor).casefold()
            filename_segment = _safe_filename(filename).casefold()
            resolved_url = f"manual-upload://{competitor_segment}/{filename_segment}"
        raw_digest = hashlib.sha256(content).hexdigest()
        temporary_raw_path = (
            project_root
            / "data"
            / "raw"
            / "manual_upload"
            / f"pending_{raw_digest[:16]}_{_safe_filename(filename)}"
        )
        try:
            document = StructuredDocument(
                raw_record_id=f"raw_{raw_digest[:24]}",
                raw_path=_relative_or_absolute(temporary_raw_path, project_root),
                competitor=resolved_competitor,
                title=resolved_title,
                content=text.strip(),
                source_type=SourceType.PRODUCT_DOCS,
                evidence_level=EvidenceLevel.C,
                url=resolved_url,
                publish_time=publish_time or None,
                crawl_time=_utc_now(),
                dimension_tags=dimension_tags or [],
                index_status=IndexStatus.PENDING,
                language="zh" if re.search(r"[\u3400-\u9fff]", text) else "en",
                source_metadata={
                    "manual_upload": True,
                    "original_filename": _safe_filename(filename),
                },
                needs_review=True,
            )
        except (ValidationError, ValueError) as exc:
            raise AdminImportError(f"Invalid document metadata: {exc}") from exc
        documents.append(document)

    # Validate duplicate and current-version invariants inside the uploaded batch.
    _validate_document_set(documents)
    raw_path = _archive_raw_upload(
        project_root=project_root,
        filename=filename,
        content=content,
    )
    if extension != ".jsonl":
        documents[0].raw_path = _relative_or_absolute(raw_path, project_root)
    return documents, raw_path


def _validate_document_set(documents: list[StructuredDocument]) -> None:
    seen_versions: set[str] = set()
    current_by_document: dict[str, str] = {}
    for document in documents:
        if document.version_id in seen_versions:
            raise AdminImportError(
                f"Duplicate version_id in upload: {document.version_id}"
            )
        seen_versions.add(document.version_id)
        if document.is_current:
            previous = current_by_document.get(document.document_id)
            if previous is not None:
                raise AdminImportError(
                    "Multiple current versions in upload for document_id "
                    f"{document.document_id}: {previous}, {document.version_id}"
                )
            current_by_document[document.document_id] = document.version_id


def _merge_documents(
    existing: list[StructuredDocument],
    incoming: list[StructuredDocument],
) -> tuple[list[StructuredDocument], int, int]:
    merged = list(existing)
    version_ids = {document.version_id for document in merged}
    imported = 0
    skipped = 0
    for document in incoming:
        if document.version_id in version_ids:
            skipped += 1
            continue
        if document.is_current:
            for previous in merged:
                if previous.document_id != document.document_id or not previous.is_current:
                    continue
                previous.is_current = False
                transition_at = document.valid_from or document.crawl_time
                previous.valid_to = max(previous.valid_from or transition_at, transition_at)
                previous.index_status = IndexStatus.STALE
        merged.append(document)
        version_ids.add(document.version_id)
        imported += 1
    _validate_document_set(merged)
    return merged, imported, skipped


def persist_documents(
    documents_path: Path,
    incoming: list[StructuredDocument],
) -> tuple[int, int, bool, Path | None]:
    """Merge, validate and atomically replace the JSONL hand-off."""

    documents_path = documents_path.resolve()
    lock_path = documents_path.with_suffix(documents_path.suffix + ".lock")
    with _process_file_lock(lock_path):
        existing = (
            DocumentLoader(documents_path).load()
            if documents_path.is_file()
            else []
        )
        merged, imported, skipped = _merge_documents(existing, incoming)
        if imported == 0:
            return imported, skipped, False, None

        payload = ("\n".join(_serialise_document(item) for item in merged) + "\n").encode(
            "utf-8"
        )
        documents_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path: Path | None = None
        if documents_path.is_file():
            backup_dir = documents_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / (
                f"{documents_path.stem}."
                f"{_utc_now().strftime('%Y%m%dT%H%M%S%fZ')}."
                f"{uuid.uuid4().hex[:8]}{documents_path.suffix}"
            )
            shutil.copy2(documents_path, backup_path)

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=documents_path.parent,
                prefix=f".{documents_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            # The exact bytes that will replace the data set must pass the public loader.
            DocumentLoader(temp_path).load()
            original_mode = (
                stat.S_IMODE(documents_path.stat().st_mode)
                if documents_path.is_file()
                else None
            )
            made_writable = (
                original_mode is not None
                and not bool(original_mode & stat.S_IWRITE)
            )
            if made_writable:
                os.chmod(documents_path, original_mode | stat.S_IWRITE)
            try:
                os.replace(temp_path, documents_path)
            finally:
                if made_writable and documents_path.exists():
                    os.chmod(documents_path, original_mode)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
        return imported, skipped, True, backup_path


def build_overview(documents_path: Path, *, recent_limit: int = 8) -> dict[str, Any]:
    documents = DocumentLoader(documents_path).load()
    statistics = DocumentLoader.statistics(documents)

    def sort_time(document: StructuredDocument) -> datetime:
        return document.publish_time or document.crawl_time

    recent = sorted(documents, key=sort_time, reverse=True)[:recent_limit]
    updated_at = datetime.fromtimestamp(
        documents_path.stat().st_mtime,
        tz=timezone.utc,
    )
    return {
        "dataset_updated_at": updated_at.isoformat().replace("+00:00", "Z"),
        "stats": {
            "documents_total": statistics.total,
            "current_versions": statistics.current,
            "historical_versions": statistics.historical,
            "pending_review": statistics.needs_review,
        },
        "distributions": {
            "competitors": statistics.competitors,
            "source_types": statistics.sources,
        },
        "recent_documents": [
            {
                "document_id": document.document_id,
                "version_id": document.version_id,
                "title": document.title,
                "competitor": document.competitor,
                "source_type": document.source_type.value,
                "publish_time": (
                    document.publish_time.isoformat().replace("+00:00", "Z")
                    if document.publish_time
                    else None
                ),
                "is_current": document.is_current,
            }
            for document in recent
        ],
    }


def list_document_summaries(
    documents_path: Path,
    *,
    page: int = 1,
    page_size: int = 20,
    query: str | None = None,
    status: str = "all",
    competitor: str | None = None,
    source_type: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic, filtered page of structured-document summaries."""

    documents = DocumentLoader(documents_path).load()
    normalized_query = (query or "").strip().casefold()
    normalized_competitor = (competitor or "").strip().casefold()
    normalized_source_type = (source_type or "").strip().casefold()

    def matches(document: StructuredDocument) -> bool:
        if normalized_query and normalized_query not in document.title.casefold():
            return False
        if (
            normalized_competitor
            and document.competitor.casefold() != normalized_competitor
        ):
            return False
        if (
            normalized_source_type
            and document.source_type.value.casefold() != normalized_source_type
        ):
            return False
        if status == "current" and not document.is_current:
            return False
        if status == "historical" and document.is_current:
            return False
        if status == "review" and not document.needs_review:
            return False
        return True

    filtered = [document for document in documents if matches(document)]

    # Include stable identity fields after the effective document timestamp so
    # repeated requests return the same order even when timestamps are equal.
    filtered.sort(
        key=lambda document: (
            document.publish_time or document.crawl_time,
            document.crawl_time,
            document.version_id,
        ),
        reverse=True,
    )
    total = len(filtered)
    offset = (page - 1) * page_size
    selected = filtered[offset : offset + page_size]

    return {
        "items": [
            {
                "document_id": document.document_id,
                "version_id": document.version_id,
                "title": document.title,
                "competitor": document.competitor,
                "source_type": document.source_type.value,
                "publish_time": document.publish_time,
                "dimension_tags": [
                    dimension.value for dimension in document.dimension_tags
                ],
                "is_current": document.is_current,
                "needs_review": document.needs_review,
            }
            for document in selected
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


def dimensions_from_form(value: str | None) -> list[str]:
    if value is None or not value.strip():
        return []
    raw = value.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise AdminImportError("dimension_tags must be a JSON array or comma-separated list.")
    valid = {tag.value for tag in DimensionTag} | {
        f"D{position}" for position in range(1, 8)
    }
    invalid = [item for item in parsed if item not in valid and item.upper() not in valid]
    if invalid:
        raise AdminImportError(f"Unknown dimension tag(s): {', '.join(invalid)}")
    return parsed


__all__ = [
    "ALLOWED_UPLOAD_EXTENSIONS",
    "MAX_UPLOAD_BYTES",
    "AdminImportError",
    "build_overview",
    "dimensions_from_form",
    "list_document_summaries",
    "parse_uploaded_documents",
    "persist_documents",
]
