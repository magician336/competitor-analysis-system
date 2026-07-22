"""Data contracts shared by the crawler and processing pipeline.

The crawler writes :class:`RawRecord` metadata next to every response payload.
The processing layer converts those records into versioned
:class:`StructuredDocument` objects.  Enum values are deliberately stable
because they are also used by later metadata filters.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Any

from dateutil import parser as date_parser
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceType(str, Enum):
    """Canonical source categories emitted by the processing layer."""

    OFFICIAL_PAGE = "official_page"
    OFFICIAL_CHANGELOG = "official_changelog"
    PRICING = "pricing"
    PRODUCT_DOCS = "product_docs"
    STATUS_PAGE = "status_page"
    GITHUB_RELEASE = "github_release"
    GITHUB_ISSUE = "github_issue"
    PLUGIN_MARKETPLACE = "plugin_marketplace"
    COMMUNITY = "community"
    REVIEW = "review"
    SECURITY_PRIVACY = "security_privacy"
    BENCHMARK = "benchmark"
    RSS = "rss"


class EventType(str, Enum):
    """Project event labels E1--E3."""

    PRICING_CHANGE = "pricing_change"
    PRODUCT_RELEASE = "product_release"
    RISK_EXPERIENCE = "risk_experience"


class DimensionTag(str, Enum):
    """Project capability labels D1--D7."""

    CODE_INTELLIGENCE = "code_intelligence"
    AGENT_CONTEXT = "agent_context"
    IDE_ECOSYSTEM = "ide_ecosystem"
    MODEL_EXTENSIBILITY = "model_extensibility"
    PERFORMANCE_COST = "performance_cost"
    SECURITY_COMPLIANCE = "security_compliance"
    EDUCATION_FIT = "education_fit"


class EvidenceLevel(str, Enum):
    """Evidence authority level, from primary official evidence to leads."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class IndexStatus(str, Enum):
    """Lifecycle state reserved for the downstream search index."""

    PENDING = "pending"
    INDEXED = "indexed"
    FAILED = "failed"
    STALE = "stale"


EVENT_CODE_MAP: dict[str, EventType] = {
    "E1": EventType.PRICING_CHANGE,
    "E2": EventType.PRODUCT_RELEASE,
    "E3": EventType.RISK_EXPERIENCE,
}

DIMENSION_CODE_MAP: dict[str, DimensionTag] = {
    "D1": DimensionTag.CODE_INTELLIGENCE,
    "D2": DimensionTag.AGENT_CONTEXT,
    "D3": DimensionTag.IDE_ECOSYSTEM,
    "D4": DimensionTag.MODEL_EXTENSIBILITY,
    "D5": DimensionTag.PERFORMANCE_COST,
    "D6": DimensionTag.SECURITY_COMPLIANCE,
    "D7": DimensionTag.EDUCATION_FIT,
}

_SOURCE_ALIASES: dict[str, SourceType] = {
    "official": SourceType.OFFICIAL_PAGE,
    "official_page": SourceType.OFFICIAL_PAGE,
    "official_website": SourceType.OFFICIAL_PAGE,
    "website": SourceType.OFFICIAL_PAGE,
    "changelog": SourceType.OFFICIAL_CHANGELOG,
    "official_changelog": SourceType.OFFICIAL_CHANGELOG,
    "rss_changelog": SourceType.OFFICIAL_CHANGELOG,
    "pricing": SourceType.PRICING,
    "product_docs": SourceType.PRODUCT_DOCS,
    "docs": SourceType.PRODUCT_DOCS,
    "documentation": SourceType.PRODUCT_DOCS,
    "status_page": SourceType.STATUS_PAGE,
    "status": SourceType.STATUS_PAGE,
    "github_release": SourceType.GITHUB_RELEASE,
    "release": SourceType.GITHUB_RELEASE,
    "github_issue": SourceType.GITHUB_ISSUE,
    "issue": SourceType.GITHUB_ISSUE,
    "plugin_marketplace": SourceType.PLUGIN_MARKETPLACE,
    "marketplace": SourceType.PLUGIN_MARKETPLACE,
    "community": SourceType.COMMUNITY,
    "forum": SourceType.COMMUNITY,
    "review": SourceType.REVIEW,
    "reviews": SourceType.REVIEW,
    "security_privacy": SourceType.SECURITY_PRIVACY,
    "security": SourceType.SECURITY_PRIVACY,
    "privacy": SourceType.SECURITY_PRIVACY,
    "trust_center": SourceType.SECURITY_PRIVACY,
    "benchmark": SourceType.BENCHMARK,
    "benchmarks": SourceType.BENCHMARK,
    "rss": SourceType.RSS,
}


def _canonical_source_type(value: Any) -> Any:
    if isinstance(value, SourceType):
        return value
    if isinstance(value, str):
        return _SOURCE_ALIASES.get(value.strip().lower(), value.strip().lower())
    return value


def _utc_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        parsed = date_parser.parse(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalise_hash(value: str) -> str:
    stripped = value.strip().lower()
    return stripped if stripped.startswith("sha256:") else f"sha256:{stripped}"


def _stable_id(prefix: str, *parts: Any) -> str:
    serialised = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(serialised.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


class RawRecord(BaseModel):
    """Metadata for one immutable raw HTTP response payload."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: str = "1.0"
    raw_record_id: str = ""
    crawl_run_id: str
    competitor: str = Field(min_length=1)
    source_type: SourceType
    requested_url: str = Field(validation_alias=AliasChoices("requested_url", "request_url", "url"))
    canonical_url: str | None = None
    final_url: str | None = None
    fetched_at: datetime = Field(validation_alias=AliasChoices("fetched_at", "crawl_time"))
    published_at: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("published_at", "publish_time"),
    )
    http_status: int | None = Field(
        default=None,
        validation_alias=AliasChoices("http_status", "status_code"),
    )
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    payload_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("payload_path", "raw_path"),
    )
    payload_hash: str | None = None
    error: str | None = None
    needs_browser: bool = False
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type", mode="before")
    @classmethod
    def normalise_source_type(cls, value: Any) -> Any:
        return _canonical_source_type(value)

    @field_validator("fetched_at", "published_at", mode="before")
    @classmethod
    def normalise_datetimes(cls, value: Any) -> datetime | None:
        return _utc_datetime(value)

    @field_validator("payload_hash")
    @classmethod
    def normalise_payload_hash(cls, value: str | None) -> str | None:
        return _normalise_hash(value) if value else None

    @model_validator(mode="after")
    def fill_stable_fields(self) -> "RawRecord":
        if not self.canonical_url:
            self.canonical_url = self.final_url or self.requested_url
        if not self.final_url:
            self.final_url = self.canonical_url or self.requested_url
        if not self.raw_record_id:
            self.raw_record_id = _stable_id(
                "raw",
                self.crawl_run_id,
                self.competitor,
                self.source_type.value,
                self.canonical_url,
                self.payload_hash,
            )
        return self

    @property
    def crawl_time(self) -> datetime:
        """Compatibility name used by the unified document schema."""

        return self.fetched_at

    @property
    def status_code(self) -> int | None:
        """Compatibility name used by HTTP clients."""

        return self.http_status


class StructuredDocument(BaseModel):
    """Clean, traceable, version-aware document consumed by Mini-RAG."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = "1.0"
    document_id: str = ""
    version_id: str = ""
    raw_record_id: str
    raw_path: str
    competitor: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_type: SourceType
    evidence_level: EvidenceLevel
    url: str
    raw_version: str | None = None
    product_version: str | None = None
    publish_time: datetime | None = None
    crawl_time: datetime
    event_type: EventType | None = None
    dimension_tags: list[DimensionTag] = Field(default_factory=list)
    content_hash: str = ""
    is_current: bool = True
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    index_status: IndexStatus = IndexStatus.PENDING
    elasticsearch_document_id: str | None = None
    language: str = "und"
    author: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    label_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    label_reasons: list[str] = Field(default_factory=list)
    needs_review: bool = False

    @field_validator("source_type", mode="before")
    @classmethod
    def normalise_source_type(cls, value: Any) -> Any:
        return _canonical_source_type(value)

    @field_validator("publish_time", "crawl_time", "valid_from", "valid_to", mode="before")
    @classmethod
    def normalise_datetimes(cls, value: Any) -> datetime | None:
        return _utc_datetime(value)

    @field_validator("event_type", mode="before")
    @classmethod
    def normalise_event_type(cls, value: Any) -> Any:
        if isinstance(value, str) and value.upper() in EVENT_CODE_MAP:
            return EVENT_CODE_MAP[value.upper()]
        return value

    @field_validator("dimension_tags", mode="before")
    @classmethod
    def normalise_dimension_tags(cls, value: Any) -> Any:
        if value is None:
            return []
        result: list[Any] = []
        for item in value:
            if isinstance(item, str) and item.upper() in DIMENSION_CODE_MAP:
                result.append(DIMENSION_CODE_MAP[item.upper()])
            else:
                result.append(item)
        return result

    @field_validator("content_hash")
    @classmethod
    def normalise_content_hash(cls, value: str) -> str:
        return _normalise_hash(value) if value else value

    @model_validator(mode="after")
    def complete_version_identity(self) -> "StructuredDocument":
        if not self.content_hash:
            digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
            self.content_hash = f"sha256:{digest}"
        if not self.document_id:
            competitor_id = str(
                self.source_metadata.get("competitor_id") or self.competitor
            ).strip().casefold()
            self.document_id = _stable_id(
                "doc",
                competitor_id,
                self.source_type.value,
                self.url,
            )
        if not self.version_id:
            self.version_id = _stable_id("ver", self.document_id, self.content_hash)
        if self.valid_from is None:
            self.valid_from = self.publish_time or self.crawl_time
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be greater than or equal to valid_from")
        self.dimension_tags = list(dict.fromkeys(self.dimension_tags))
        self.label_reasons = list(dict.fromkeys(self.label_reasons))
        return self

    @property
    def event_code(self) -> str | None:
        if self.event_type is None:
            return None
        return next(code for code, event in EVENT_CODE_MAP.items() if event == self.event_type)

    @property
    def dimension_codes(self) -> list[str]:
        reverse = {tag: code for code, tag in DIMENSION_CODE_MAP.items()}
        return [reverse[tag] for tag in self.dimension_tags]
