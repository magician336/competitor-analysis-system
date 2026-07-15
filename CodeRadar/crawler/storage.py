"""Raw response persistence for the acquisition boundary."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import requests

from .models import RawRecord, SourceType, utc_now


_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_component(value: str) -> str:
    normalized = _SAFE_COMPONENT.sub("_", value.strip()).strip("._")
    return normalized or "unknown"


def _extension_for_content_type(content_type: str | None) -> str:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type in {"application/json", "application/ld+json"} or media_type.endswith(
        "+json"
    ):
        return ".json"
    if media_type in {
        "application/rss+xml",
        "application/atom+xml",
        "application/xml",
        "text/xml",
    } or media_type.endswith("+xml"):
        return ".xml"
    if media_type in {"text/plain", "text/markdown"}:
        return ".txt"
    return ".html"


class RawWriter:
    """Persist payloads and sidecar metadata under one crawl-run directory."""

    def __init__(self, raw_root: str | Path, crawl_run_id: str) -> None:
        self.raw_root = Path(raw_root).resolve()
        self.crawl_run_id = _safe_component(crawl_run_id)

    def _record_id(
        self,
        competitor: str,
        source_type: SourceType,
        canonical_url: str,
        payload_hash: str | None,
    ) -> str:
        identity = "\0".join(
            (
                self.crawl_run_id,
                competitor,
                source_type.value,
                canonical_url,
                payload_hash or "no-payload",
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    def _directory(self, competitor: str, source_type: SourceType) -> Path:
        directory = (
            self.raw_root
            / _safe_component(competitor)
            / _safe_component(source_type.value)
            / self.crawl_run_id
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)

    def _write_record(self, record: RawRecord) -> RawRecord:
        directory = self._directory(record.competitor, record.source_type)
        metadata_path = directory / f"{record.raw_record_id}.meta.json"
        body = json.dumps(
            record.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        self._atomic_write(metadata_path, body)
        return record

    def write_bytes(
        self,
        *,
        competitor: str,
        source_type: SourceType | str,
        requested_url: str,
        canonical_url: str,
        payload: bytes | None,
        final_url: str | None = None,
        http_status: int | None = 200,
        content_type: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        published_at: datetime | None = None,
        error: str | None = None,
        needs_browser: bool = False,
        source_metadata: Mapping[str, Any] | None = None,
        extension: str | None = None,
        fetched_at: datetime | None = None,
    ) -> RawRecord:
        parsed_source_type = SourceType.parse(source_type)
        payload_hash = hashlib.sha256(payload).hexdigest() if payload is not None else None
        raw_record_id = self._record_id(
            competitor, parsed_source_type, canonical_url, payload_hash
        )
        payload_path: str | None = None
        if payload is not None:
            suffix = extension or _extension_for_content_type(content_type)
            if not suffix.startswith("."):
                suffix = f".{suffix}"
            target = self._directory(competitor, parsed_source_type) / (
                raw_record_id + suffix
            )
            self._atomic_write(target, payload)
            payload_path = target.relative_to(self.raw_root).as_posix()

        return self._write_record(
            RawRecord(
                raw_record_id=raw_record_id,
                crawl_run_id=self.crawl_run_id,
                competitor=competitor,
                source_type=parsed_source_type,
                requested_url=requested_url,
                canonical_url=canonical_url,
                final_url=final_url,
                fetched_at=fetched_at or utc_now(),
                published_at=published_at,
                http_status=http_status,
                content_type=content_type,
                etag=etag,
                last_modified=last_modified,
                payload_path=payload_path,
                payload_hash=payload_hash,
                error=error,
                needs_browser=needs_browser,
                source_metadata=dict(source_metadata or {}),
            )
        )

    def write_json(self, *, payload: Any, **kwargs: Any) -> RawRecord:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        kwargs.setdefault("content_type", "application/json")
        kwargs.setdefault("extension", ".json")
        return self.write_bytes(payload=body, **kwargs)

    def write_response(
        self,
        *,
        competitor: str,
        source_type: SourceType | str,
        requested_url: str,
        response: requests.Response,
        canonical_url: str | None = None,
        published_at: datetime | None = None,
        needs_browser: bool = False,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> RawRecord:
        payload = None if response.status_code == 304 else response.content
        return self.write_bytes(
            competitor=competitor,
            source_type=source_type,
            requested_url=requested_url,
            canonical_url=canonical_url or response.url or requested_url,
            final_url=response.url,
            payload=payload,
            http_status=response.status_code,
            content_type=response.headers.get("Content-Type"),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            published_at=published_at,
            needs_browser=needs_browser,
            source_metadata=source_metadata,
        )

    def write_error(
        self,
        *,
        competitor: str,
        source_type: SourceType | str,
        requested_url: str,
        error: str,
        final_url: str | None = None,
        http_status: int | None = None,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> RawRecord:
        return self.write_bytes(
            competitor=competitor,
            source_type=source_type,
            requested_url=requested_url,
            canonical_url=final_url or requested_url,
            final_url=final_url,
            payload=None,
            http_status=http_status,
            error=error,
            source_metadata=source_metadata,
        )
