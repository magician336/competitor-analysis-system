"""Shared, serialisable contracts for the CodeRadar Mini-RAG pipeline.

The models in this module are deliberately independent from Elasticsearch and
from any embedding provider.  Ingestion, retrieval, evidence validation and
offline evaluation therefore exchange the same validated objects in both the
in-memory and Elasticsearch-backed execution paths.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from schemas.document import (
    DIMENSION_CODE_MAP,
    EVENT_CODE_MAP,
    DimensionTag,
    EventType,
    EvidenceLevel,
    SourceType,
)


_SOURCE_ALIASES: dict[str, SourceType] = {
    "official": SourceType.OFFICIAL_PAGE,
    "changelog": SourceType.OFFICIAL_CHANGELOG,
    "pricing": SourceType.PRICING,
    "release": SourceType.GITHUB_RELEASE,
    "issue": SourceType.GITHUB_ISSUE,
}

_COMPETITOR_ALIASES: dict[str, str] = {
    "cursor": "Cursor",
    "cursor ai": "Cursor",
    "github copilot": "GitHub Copilot",
    "copilot": "GitHub Copilot",
    "trae": "Trae",
    "通义灵码": "通义灵码",
    "灵码": "通义灵码",
    "tongyi lingma": "通义灵码",
    "lingma": "通义灵码",
    "qoder": "通义灵码",
    "codegeex": "CodeGeeX",
    "code geex": "CodeGeeX",
}


def _stable_id(prefix: str, *parts: Any) -> str:
    """Return a compact deterministic identifier for JSON-compatible values."""

    payload = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _sha256(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _deduplicate(values: list[Any]) -> list[Any]:
    """Deduplicate hashable enum/string values while retaining input order."""

    return list(dict.fromkeys(values))


def _normalise_product_version(value: Any) -> str | None:
    """Use the ingestion normalizer for both indexed and queried versions."""

    if value in (None, ""):
        return None
    from processing.normalizers import normalize_version

    normalized = normalize_version(value)
    return normalized or str(value).strip() or None


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Chunk(_Contract):
    """One source-locatable retrieval unit derived from a document version."""

    schema_version: str = "1.0"
    chunk_id: str = ""
    document_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    raw_record_id: str = Field(min_length=1)
    raw_path: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    section_type: str = "content"

    competitor: str = Field(min_length=1)
    source_type: SourceType
    evidence_level: EvidenceLevel
    url: str
    raw_version: str | None = None
    product_version: str | None = None
    publish_time: datetime | None = None
    crawl_time: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_current: bool = True
    event_type: EventType | None = None
    dimension_tags: list[DimensionTag] = Field(default_factory=list)
    language: str = "und"
    author: str | None = None

    # ``content_hash`` identifies the complete source document. ``chunk_hash``
    # identifies this exact source slice and is used in deterministic IDs.
    content_hash: str = ""
    chunk_hash: str = ""
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    # Source-specific fields stay optional so all index records share one
    # mapping while retaining pricing and GitHub citation details.
    change_category: str | None = None
    plan_name: str | None = None
    price_value: float | None = None
    currency: str | None = None
    billing_period: str | None = None
    applicable_plans: list[str] = Field(default_factory=list)
    repository: str | None = None
    github_kind: str | None = None
    github_number: int | None = Field(default=None, ge=0)
    github_labels: list[str] = Field(default_factory=list)
    github_state: str | None = None
    author_type: str | None = None
    comment_url: str | None = None
    comment_time: datetime | None = None

    embedding: list[float] | None = None
    embedding_model: str | None = None
    embedding_dimension: int | None = Field(default=None, gt=0)

    @field_validator("source_type", mode="before")
    @classmethod
    def normalise_source_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _SOURCE_ALIASES.get(value.strip().lower(), value.strip().lower())
        return value

    @field_validator("evidence_level", mode="before")
    @classmethod
    def normalise_evidence_level(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("event_type", mode="before")
    @classmethod
    def normalise_event_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return EVENT_CODE_MAP.get(value.strip().upper(), value.strip().lower())
        return value

    @field_validator("product_version", mode="before")
    @classmethod
    def normalise_product_version(cls, value: Any) -> Any:
        return _normalise_product_version(value)

    @field_validator("dimension_tags", mode="before")
    @classmethod
    def normalise_dimension_codes(cls, values: Any) -> Any:
        if values is None:
            return []
        return [
            DIMENSION_CODE_MAP.get(value.strip().upper(), value.strip().lower())
            if isinstance(value, str)
            else value
            for value in values
        ]

    @field_validator(
        "heading_path",
        "applicable_plans",
        "github_labels",
        mode="after",
    )
    @classmethod
    def normalise_string_lists(cls, values: list[str]) -> list[str]:
        return _deduplicate([value.strip() for value in values if value.strip()])

    @field_validator("dimension_tags", mode="after")
    @classmethod
    def deduplicate_dimension_tags(
        cls, values: list[DimensionTag]
    ) -> list[DimensionTag]:
        return _deduplicate(values)

    @model_validator(mode="after")
    def complete_identity_and_validate_locator(self) -> "Chunk":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.char_end - self.char_start != len(self.content):
            raise ValueError(
                "char_start and char_end must describe the exact content slice"
            )
        if not self.chunk_hash:
            self.chunk_hash = _sha256(self.content)
        if self.content_hash and not self.content_hash.startswith("sha256:"):
            self.content_hash = f"sha256:{self.content_hash.strip().lower()}"
        if not self.content_hash:
            self.content_hash = self.chunk_hash
        if not self.chunk_id:
            self.chunk_id = _stable_id(
                "chunk",
                self.document_id,
                self.version_id,
                self.heading_path,
                self.section_type,
                self.chunk_index,
                self.char_start,
                self.char_end,
                self.chunk_hash,
            )
        if self.embedding is not None:
            if not self.embedding:
                raise ValueError("embedding must contain at least one value")
            if self.embedding_dimension is None:
                self.embedding_dimension = len(self.embedding)
            elif self.embedding_dimension != len(self.embedding):
                raise ValueError("embedding_dimension does not match embedding length")
        return self

    @computed_field
    @property
    def source_locator(self) -> str:
        """Canonical character-range locator used by citation validation."""

        return f"{self.url}#chars={self.char_start}-{self.char_end}"


class SearchCandidate(_Contract):
    """A Chunk plus the scores and ranks produced by retrieval stages."""

    chunk: Chunk
    bm25_rank: int | None = Field(default=None, ge=1)
    bm25_score: float | None = None
    dense_rank: int | None = Field(default=None, ge=1)
    dense_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    temporal_score: float | None = None
    version_score: float | None = None
    evidence_score: float | None = None
    final_score: float | None = None
    retrieval_methods: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("retrieval_methods", mode="after")
    @classmethod
    def deduplicate_methods(cls, values: list[str]) -> list[str]:
        return _deduplicate([value.strip().lower() for value in values if value.strip()])

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id

    @property
    def document_id(self) -> str:
        return self.chunk.document_id

    @property
    def content(self) -> str:
        return self.chunk.content


class RAGQuery(_Contract):
    """Validated query and metadata filters accepted by Mini-RAG."""

    question: str = Field(min_length=1)
    competitor: str | None = None
    event_types: list[EventType] = Field(default_factory=list)
    dimension_tags: list[DimensionTag] = Field(default_factory=list)
    product_versions: list[str] = Field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    evidence_levels: list[EvidenceLevel] = Field(default_factory=list)
    source_types: list[SourceType] = Field(default_factory=list)
    current_only: bool = False
    top_k: int = Field(default=8, ge=1, le=100)

    @field_validator("competitor", mode="before")
    @classmethod
    def normalise_competitor(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return _COMPETITOR_ALIASES.get(stripped.casefold(), stripped) or None

    @field_validator("event_types", mode="before")
    @classmethod
    def normalise_event_codes(cls, values: Any) -> Any:
        if values is None:
            return []
        return [
            EVENT_CODE_MAP.get(value.strip().upper(), value.strip().lower())
            if isinstance(value, str)
            else value
            for value in values
        ]

    @field_validator("dimension_tags", mode="before")
    @classmethod
    def normalise_dimension_codes(cls, values: Any) -> Any:
        if values is None:
            return []
        return [
            DIMENSION_CODE_MAP.get(value.strip().upper(), value.strip().lower())
            if isinstance(value, str)
            else value
            for value in values
        ]

    @field_validator("product_versions", mode="before")
    @classmethod
    def normalise_product_versions(cls, values: Any) -> list[str]:
        if values is None:
            return []
        if isinstance(values, (str, int, float)):
            values = [values]
        return [
            normalized
            for value in values
            if (normalized := _normalise_product_version(value)) is not None
        ]

    @field_validator("evidence_levels", mode="before")
    @classmethod
    def normalise_evidence_levels(cls, values: Any) -> Any:
        if values is None:
            return []
        return [value.strip().upper() if isinstance(value, str) else value for value in values]

    @field_validator("source_types", mode="before")
    @classmethod
    def normalise_source_types(cls, values: Any) -> Any:
        if values is None:
            return []
        return [
            _SOURCE_ALIASES.get(value.strip().lower(), value.strip().lower())
            if isinstance(value, str)
            else value
            for value in values
        ]

    @field_validator("question", mode="after")
    @classmethod
    def strip_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped

    @field_validator(
        "event_types",
        "dimension_tags",
        "product_versions",
        "evidence_levels",
        "source_types",
        mode="after",
    )
    @classmethod
    def deduplicate_filters(cls, values: list[Any]) -> list[Any]:
        return _deduplicate(values)

    @model_validator(mode="after")
    def validate_time_range(self) -> "RAGQuery":
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValueError("end_time must be greater than or equal to start_time")
        return self

    def filters(self, *, exclude_empty: bool = True) -> dict[str, Any]:
        """Return the query fields that constrain retrieval."""

        names = (
            "competitor",
            "event_types",
            "dimension_tags",
            "product_versions",
            "start_time",
            "end_time",
            "evidence_levels",
            "source_types",
            "current_only",
        )
        values = self.model_dump(mode="json", include=set(names))
        if not exclude_empty:
            return values
        return {
            key: value
            for key, value in values.items()
            if value not in (None, [], "", False)
        }


class Evidence(_Contract):
    """Agent-facing evidence with citation and ranking information."""

    citation_id: str = ""
    chunk_id: str
    document_id: str
    version_id: str
    content: str
    quote: str | None = None
    title: str
    url: str
    heading_path: list[str] = Field(default_factory=list)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    competitor: str
    source_type: SourceType
    evidence_level: EvidenceLevel
    event_type: EventType | None = None
    dimension_tags: list[DimensionTag] = Field(default_factory=list)
    product_version: str | None = None
    publish_time: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_current: bool = True
    bm25_rank: int | None = Field(default=None, ge=1)
    dense_rank: int | None = Field(default=None, ge=1)
    rrf_score: float | None = None
    rerank_score: float | None = None
    temporal_score: float | None = None
    version_score: float | None = None
    evidence_score: float | None = None
    final_score: float | None = None
    retrieval_methods: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type", mode="before")
    @classmethod
    def normalise_source_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _SOURCE_ALIASES.get(value.strip().lower(), value.strip().lower())
        return value

    @field_validator("evidence_level", mode="before")
    @classmethod
    def normalise_evidence_level(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("event_type", mode="before")
    @classmethod
    def normalise_event_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return EVENT_CODE_MAP.get(value.strip().upper(), value.strip().lower())
        return value

    @field_validator("dimension_tags", mode="before")
    @classmethod
    def normalise_dimension_codes(cls, values: Any) -> Any:
        if values is None:
            return []
        return [
            DIMENSION_CODE_MAP.get(value.strip().upper(), value.strip().lower())
            if isinstance(value, str)
            else value
            for value in values
        ]

    @model_validator(mode="after")
    def complete_citation(self) -> "Evidence":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.quote is None:
            self.quote = self.content
        if not self.citation_id:
            self.citation_id = _stable_id(
                "cite", self.chunk_id, self.char_start, self.char_end, self.quote
            )
        return self

    @classmethod
    def from_candidate(cls, candidate: SearchCandidate) -> "Evidence":
        chunk = candidate.chunk
        return cls(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            version_id=chunk.version_id,
            content=chunk.content,
            title=chunk.title,
            url=chunk.url,
            heading_path=chunk.heading_path,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            competitor=chunk.competitor,
            source_type=chunk.source_type,
            evidence_level=chunk.evidence_level,
            event_type=chunk.event_type,
            dimension_tags=chunk.dimension_tags,
            product_version=chunk.product_version,
            publish_time=chunk.publish_time,
            valid_from=chunk.valid_from,
            valid_to=chunk.valid_to,
            is_current=chunk.is_current,
            bm25_rank=candidate.bm25_rank,
            dense_rank=candidate.dense_rank,
            rrf_score=candidate.rrf_score,
            rerank_score=candidate.rerank_score,
            temporal_score=candidate.temporal_score,
            version_score=candidate.version_score,
            evidence_score=candidate.evidence_score,
            final_score=candidate.final_score,
            retrieval_methods=candidate.retrieval_methods,
            metadata={
                **candidate.metadata,
                "section_type": chunk.section_type,
                "source_locator": chunk.source_locator,
            },
        )


class Conflict(_Contract):
    """Competing values for one fact, retained instead of silently overwritten."""

    conflict_id: str = ""
    field: str = Field(min_length=1)
    competitor: str | None = None
    values: list[str] = Field(min_length=2)
    chunk_ids: list[str] = Field(min_length=2)
    product_versions: list[str | None] = Field(default_factory=list)
    publish_times: list[datetime | None] = Field(default_factory=list)
    preferred_chunk_id: str | None = None
    reason: str = ""

    @field_validator("values", "chunk_ids", mode="after")
    @classmethod
    def retain_unique_values(cls, values: list[str]) -> list[str]:
        return _deduplicate(values)

    @model_validator(mode="after")
    def complete_conflict_id(self) -> "Conflict":
        if len(self.values) < 2:
            raise ValueError("a conflict requires at least two distinct values")
        if len(self.chunk_ids) < 2:
            raise ValueError("a conflict requires at least two distinct chunks")
        if self.preferred_chunk_id and self.preferred_chunk_id not in self.chunk_ids:
            raise ValueError("preferred_chunk_id must be one of chunk_ids")
        if not self.conflict_id:
            self.conflict_id = _stable_id(
                "conflict", self.field, self.competitor, self.values, self.chunk_ids
            )
        return self


class RetrievalTrace(_Contract):
    """Observable candidate counts and latency for one retrieval request."""

    bm25_candidates: int = Field(default=0, ge=0)
    dense_candidates: int = Field(default=0, ge=0)
    fused_candidates: int = Field(default=0, ge=0)
    reranked_candidates: int = Field(default=0, ge=0)
    returned_candidates: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    stage_latency_ms: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    retrieval_config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("stage_latency_ms", mode="after")
    @classmethod
    def validate_stage_latencies(cls, values: dict[str, float]) -> dict[str, float]:
        if any(value < 0 for value in values.values()):
            raise ValueError("stage latencies must be non-negative")
        return values


class RAGResponse(_Contract):
    """Stable response returned by the Python and HTTP Mini-RAG interfaces."""

    query_id: str = ""
    query: str = Field(
        min_length=1,
        validation_alias=AliasChoices("query", "question"),
    )
    parsed_filters: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    retrieval_trace: RetrievalTrace = Field(default_factory=RetrievalTrace)

    @model_validator(mode="after")
    def complete_query_id(self) -> "RAGResponse":
        self.query = self.query.strip()
        if not self.query_id:
            self.query_id = f"qry_{uuid4().hex}"
        if self.retrieval_trace.returned_candidates == 0 and self.evidence:
            self.retrieval_trace.returned_candidates = len(self.evidence)
        return self


class CitationValidationResult(_Contract):
    """Result of validating Agent citations against one retrieval response."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validated_chunk_ids: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)

    @model_validator(mode="after")
    def align_validity_with_errors(self) -> "CitationValidationResult":
        self.errors = _deduplicate(self.errors)
        self.warnings = _deduplicate(self.warnings)
        self.validated_chunk_ids = _deduplicate(self.validated_chunk_ids)
        if self.errors:
            self.valid = False
        return self


class EvaluationCase(_Contract):
    """One labelled offline retrieval query."""

    case_id: str = ""
    question: str = Field(min_length=1)
    relevant_document_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "relevant_document_ids", "expected_document_ids"
        ),
    )
    relevant_chunk_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("relevant_chunk_ids", "expected_chunk_ids"),
    )
    relevance_grades: dict[str, float] = Field(default_factory=dict)
    expected_competitor: str | None = None
    expected_event_type: EventType | None = None
    expected_dimension_tags: list[DimensionTag] = Field(default_factory=list)
    expected_version: str | None = None
    expected_evidence_quote: str | None = None
    query_filters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_event_type", mode="before")
    @classmethod
    def normalise_expected_event_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return EVENT_CODE_MAP.get(value.strip().upper(), value.strip().lower())
        return value

    @field_validator("expected_dimension_tags", mode="before")
    @classmethod
    def normalise_expected_dimension_tags(cls, values: Any) -> Any:
        if values is None:
            return []
        return [
            DIMENSION_CODE_MAP.get(value.strip().upper(), value.strip().lower())
            if isinstance(value, str)
            else value
            for value in values
        ]

    @model_validator(mode="after")
    def complete_case_id(self) -> "EvaluationCase":
        self.question = self.question.strip()
        self.relevant_document_ids = _deduplicate(self.relevant_document_ids)
        self.relevant_chunk_ids = _deduplicate(self.relevant_chunk_ids)
        self.expected_dimension_tags = _deduplicate(self.expected_dimension_tags)
        if not self.case_id:
            self.case_id = _stable_id(
                "case",
                self.question,
                self.relevant_document_ids,
                self.relevant_chunk_ids,
            )
        return self

    @property
    def expected_document_ids(self) -> list[str]:
        return self.relevant_document_ids

    @property
    def expected_chunk_ids(self) -> list[str]:
        return self.relevant_chunk_ids


class EvaluationCaseResult(_Contract):
    """Metrics and retrieved IDs for one :class:`EvaluationCase`."""

    case_id: str
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    latency_ms: float = Field(default=0.0, ge=0.0)
    citation_valid: bool | None = None
    errors: list[str] = Field(default_factory=list)


class EvaluationResult(_Contract):
    """Aggregate result for one reproducible offline evaluation run."""

    evaluation_id: str = ""
    metrics: dict[str, float] = Field(default_factory=dict)
    case_count: int = Field(default=0, ge=0)
    latencies_ms: list[float] = Field(default_factory=list)
    details: list[EvaluationCaseResult] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator("latencies_ms", mode="after")
    @classmethod
    def validate_latencies(cls, values: list[float]) -> list[float]:
        if any(value < 0 for value in values):
            raise ValueError("latencies must be non-negative")
        return values

    @model_validator(mode="after")
    def complete_evaluation_identity(self) -> "EvaluationResult":
        if self.completed_at and self.started_at and self.completed_at < self.started_at:
            raise ValueError("completed_at must be greater than or equal to started_at")
        if self.case_count == 0 and self.details:
            self.case_count = len(self.details)
        if not self.evaluation_id:
            self.evaluation_id = _stable_id(
                "eval", self.started_at, self.config, [item.case_id for item in self.details]
            )
        return self


__all__ = [
    "Chunk",
    "CitationValidationResult",
    "Conflict",
    "EvaluationCase",
    "EvaluationCaseResult",
    "EvaluationResult",
    "Evidence",
    "RAGQuery",
    "RAGResponse",
    "RetrievalTrace",
    "SearchCandidate",
]
