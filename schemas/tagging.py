"""Strict contracts for the hybrid E1--E3 / D1--D7 tagging Agent.

The rule labeler and the optional LLM labeler intentionally have separate
fields.  Downstream code can therefore audit disagreements without losing the
deterministic second-week baseline.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .document import DIMENSION_CODE_MAP, EVENT_CODE_MAP, DimensionTag, EventType, SourceType


class TagMergeStrategy(str, Enum):
    """How the final D1--D7 tag list is selected in hybrid mode."""

    CONSENSUS = "consensus"
    UNION = "union"


def _normalise_event(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return EVENT_CODE_MAP.get(stripped.upper(), stripped.casefold())
    return value


def _normalise_tags(values: Any) -> Any:
    if values is None:
        return []
    if isinstance(values, (str, DimensionTag)):
        values = [values]
    return [
        DIMENSION_CODE_MAP.get(value.strip().upper(), value.strip().casefold())
        if isinstance(value, str)
        else value
        for value in values
    ]


def _deduplicate_text(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class DimensionTaggingRequest(BaseModel):
    """One untrusted document fragment to classify."""

    model_config = ConfigDict(extra="forbid")

    source_type: SourceType
    title: str = Field(min_length=1, max_length=1_000)
    content: str = Field(min_length=1, max_length=100_000)
    strategy: TagMergeStrategy = TagMergeStrategy.UNION
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("title", "content")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("tagging input must not be blank")
        return stripped

    @field_validator("correlation_id")
    @classmethod
    def strip_correlation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("correlation_id must not be blank")
        return stripped


class DimensionTaggingDraft(BaseModel):
    """Strict structured output accepted from an injected LLM client."""

    model_config = ConfigDict(extra="forbid")

    event_type: EventType | None = None
    dimension_tags: list[DimensionTag] = Field(default_factory=list, max_length=7)
    confidence_score: float = Field(ge=0.0, le=1.0)
    label_reasons: list[str] = Field(default_factory=list, max_length=20)
    needs_review: bool = False

    @field_validator("event_type", mode="before")
    @classmethod
    def normalise_event(cls, value: Any) -> Any:
        return _normalise_event(value)

    @field_validator("dimension_tags", mode="before")
    @classmethod
    def normalise_dimension_codes(cls, values: Any) -> Any:
        return _normalise_tags(values)

    @field_validator("dimension_tags", mode="after")
    @classmethod
    def deduplicate_tags(cls, values: list[DimensionTag]) -> list[DimensionTag]:
        return list(dict.fromkeys(values))

    @field_validator("label_reasons", mode="after")
    @classmethod
    def clean_reasons(cls, values: list[str]) -> list[str]:
        return _deduplicate_text(values)

    @model_validator(mode="after")
    def flag_unsupported_confidence(self) -> "DimensionTaggingDraft":
        if not self.dimension_tags or self.event_type is None or not self.label_reasons:
            self.needs_review = True
        return self


class DimensionTaggingResult(BaseModel):
    """Auditable rule, Agent and merged labels for one request."""

    model_config = ConfigDict(extra="forbid")

    request: DimensionTaggingRequest
    strategy: TagMergeStrategy
    analysis_mode: Literal["rules", "hybrid", "hybrid_fallback"]

    # Deterministic baseline.  These fields are never replaced by Agent output.
    rule_event: EventType | None = None
    rule_tags: list[DimensionTag] = Field(default_factory=list)
    rule_confidence: float = Field(ge=0.0, le=1.0)
    rule_reasons: list[str] = Field(default_factory=list)
    rule_needs_review: bool = False

    # Independent LLM judgment.  Empty/None means that no valid draft existed.
    agent_event: EventType | None = None
    agent_tags: list[DimensionTag] = Field(default_factory=list)
    agent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    agent_reasons: list[str] = Field(default_factory=list)
    agent_needs_review: bool | None = None

    agreement: bool | None = None
    consensus_event: EventType | None = None
    consensus_tags: list[DimensionTag] = Field(default_factory=list)
    union_tags: list[DimensionTag] = Field(default_factory=list)
    final_event: EventType | None = None
    final_tags: list[DimensionTag] = Field(default_factory=list)
    final_confidence: float = Field(ge=0.0, le=1.0)
    final_reasons: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    needs_review: bool = False
    warnings: list[str] = Field(default_factory=list)
    prompt_name: str = "dimension_tagging_prompt.md"

    @field_validator(
        "rule_tags",
        "agent_tags",
        "consensus_tags",
        "union_tags",
        "final_tags",
        mode="after",
    )
    @classmethod
    def deduplicate_tags(cls, values: list[DimensionTag]) -> list[DimensionTag]:
        return list(dict.fromkeys(values))

    @field_validator(
        "rule_reasons",
        "agent_reasons",
        "final_reasons",
        "disagreements",
        "warnings",
        mode="after",
    )
    @classmethod
    def clean_text_lists(cls, values: list[str]) -> list[str]:
        return _deduplicate_text(values)

    @model_validator(mode="after")
    def validate_audit_state(self) -> "DimensionTaggingResult":
        has_agent_result = self.agent_confidence is not None
        if self.analysis_mode == "hybrid" and not has_agent_result:
            raise ValueError("hybrid mode requires a validated Agent result")
        if self.analysis_mode in {"rules", "hybrid_fallback"} and has_agent_result:
            raise ValueError(f"{self.analysis_mode} mode must not contain Agent labels")
        if self.analysis_mode == "rules" and self.agreement is not None:
            raise ValueError("rules mode has no cross-labeler agreement value")
        if self.disagreements or self.analysis_mode == "hybrid_fallback":
            self.needs_review = True
        return self


__all__ = [
    "DimensionTaggingDraft",
    "DimensionTaggingRequest",
    "DimensionTaggingResult",
    "TagMergeStrategy",
]
