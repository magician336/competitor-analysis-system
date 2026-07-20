"""Benchmark task and manual run schemas for week-three delivery."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .document import DimensionTag


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_FAIRNESS_CONSTRAINTS = [
    "所有参评产品使用相同任务说明、起始文件和验收标准",
    "记录产品版本、模型、运行时间、交互轮次和人工干预",
]


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


class BenchmarkTaskType(str, Enum):
    COMPLETION = "completion"
    COMPILE_FIX = "compile_fix"
    TEST_FIX = "test_fix"
    MULTI_FILE_CHANGE = "multi_file_change"
    REFACTOR = "refactor"
    EXPLANATION = "explanation"
    TEST_GENERATION = "test_generation"
    SECURITY_REVIEW = "security_review"


class BenchmarkDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class BenchmarkTask(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    task_id: str = ""
    name: str = Field(min_length=1)
    task_type: BenchmarkTaskType
    language: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    repository_path: str | None = None
    validation_command: str | None = None
    difficulty: BenchmarkDifficulty = BenchmarkDifficulty.MEDIUM
    expected_behavior: str = Field(min_length=1)
    # Defaults keep older callers and generated API clients compatible; the
    # before-validator derives non-empty values from the legacy fields.
    success_criteria: list[str] = Field(default_factory=list, min_length=1)
    validation_method: str = Field(default="", min_length=1)
    fairness_constraints: list[str] = Field(default_factory=list, min_length=1)
    task_revision: str = Field(default="1.0.0", min_length=1)
    protocol_version: str = Field(default="week3-manual-v1", min_length=1)
    task_fingerprint: str = ""
    primary_dimensions: list[DimensionTag] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def populate_reproducibility_metadata(cls, value: Any) -> Any:
        """Keep older task payloads valid while making the protocol explicit."""

        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        expected = str(payload.get("expected_behavior") or "").strip()
        validation_command = str(payload.get("validation_command") or "").strip()
        payload.setdefault("success_criteria", [expected] if expected else [])
        payload.setdefault(
            "validation_method",
            validation_command or "按成功标准执行双人独立人工评审",
        )
        payload.setdefault("fairness_constraints", list(_DEFAULT_FAIRNESS_CONSTRAINTS))
        return payload

    @field_validator("task_id", mode="before")
    @classmethod
    def validate_optional_task_id(cls, value: Any) -> str:
        if value is None or value == "":
            return ""
        normalized = str(value).strip()
        if not normalized or not _IDENTIFIER_PATTERN.fullmatch(normalized):
            raise ValueError("task_id must be a non-blank portable identifier")
        return normalized

    @field_validator("task_fingerprint", mode="before")
    @classmethod
    def validate_optional_fingerprint(cls, value: Any) -> str:
        if value is None or value == "":
            return ""
        normalized = str(value).strip().lower()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", normalized):
            raise ValueError("task_fingerprint must use sha256:<64 lowercase hex>")
        return normalized

    @field_validator(
        "name",
        "language",
        "prompt",
        "expected_behavior",
        "validation_method",
        "task_revision",
        "protocol_version",
        mode="after",
    )
    @classmethod
    def reject_blank_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("repository_path", "validation_command", mode="after")
    @classmethod
    def reject_blank_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("optional text must be null or non-blank")
        return value

    @field_validator("repository_path", mode="after")
    @classmethod
    def validate_repository_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = value.replace("\\", "/")
        if path.startswith("/") or re.match(r"^[A-Za-z]:/", path):
            raise ValueError("repository_path must be relative to the repository root")
        if ".." in path.split("/"):
            raise ValueError("repository_path must not traverse outside the repository")
        return path

    @field_validator("primary_dimensions", mode="after")
    @classmethod
    def deduplicate_dimensions(cls, values: list[Any]) -> list[Any]:
        return list(dict.fromkeys(values))

    @field_validator("success_criteria", "fairness_constraints", "tags", mode="after")
    @classmethod
    def normalize_text_lists(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            text = value.strip()
            if not text:
                raise ValueError("list values must not be blank")
            if text not in normalized:
                normalized.append(text)
        return normalized

    @model_validator(mode="after")
    def complete_identity(self) -> "BenchmarkTask":
        if not self.task_id:
            self.task_id = _stable_id(
                "task", self.name, self.task_type.value, self.language
            )
        fingerprint_payload = {
            "task_id": self.task_id,
            "name": self.name,
            "task_type": self.task_type.value,
            "language": self.language,
            "prompt": self.prompt,
            "repository_path": self.repository_path,
            "validation_command": self.validation_command,
            "difficulty": self.difficulty.value,
            "expected_behavior": self.expected_behavior,
            "success_criteria": self.success_criteria,
            "validation_method": self.validation_method,
            "fairness_constraints": self.fairness_constraints,
            "task_revision": self.task_revision,
            "protocol_version": self.protocol_version,
            "primary_dimensions": [item.value for item in self.primary_dimensions],
            "tags": self.tags,
        }
        canonical = json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_fingerprint = "sha256:" + hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        if self.task_fingerprint and self.task_fingerprint != expected_fingerprint:
            raise ValueError("task_fingerprint does not match canonical task metadata")
        self.task_fingerprint = expected_fingerprint
        return self


class BenchmarkRun(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    run_id: str = ""
    competitor: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=128)
    task_revision: str = Field(min_length=1, max_length=64)
    task_fingerprint: str
    validator_sha256: str
    protocol_sha256: str
    starter_sha256: str
    candidate_sha256: str
    product_version: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    task_success: bool = Field(default=False, strict=True)
    compile_success: bool | None = Field(default=None, strict=True)
    test_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    edit_rounds: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    manual_intervention: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0.0)
    harmful_action: bool = Field(default=False, strict=True)
    notes: str = ""
    run_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("run_id", mode="before")
    @classmethod
    def validate_optional_run_id(cls, value: Any) -> str:
        if value is None or value == "":
            return ""
        normalized = str(value).strip()
        if not normalized or not _IDENTIFIER_PATTERN.fullmatch(normalized):
            raise ValueError("run_id must be a non-blank portable identifier")
        return normalized

    @field_validator("task_id", mode="after")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("task_id must be a portable identifier")
        return value

    @field_validator("task_revision", mode="after")
    @classmethod
    def reject_blank_task_revision(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task_revision must not be blank")
        return value

    @field_validator("task_fingerprint", mode="after")
    @classmethod
    def validate_task_fingerprint(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError(
                "task_fingerprint must use sha256:<64 lowercase hex>"
            )
        return value

    @field_validator(
        "validator_sha256",
        "protocol_sha256",
        "starter_sha256",
        "candidate_sha256",
        mode="after",
    )
    @classmethod
    def validate_content_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("content hashes must use 64 lowercase hex characters")
        return value

    @field_validator("competitor", mode="after")
    @classmethod
    def reject_blank_competitor(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("competitor must not be blank")
        return value

    @field_validator("product_version", "model", mode="after")
    @classmethod
    def reject_blank_optional_run_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("optional text must be null or non-blank")
        return value

    @field_validator("run_at", mode="before")
    @classmethod
    def reject_blank_time(cls, value: Any) -> Any:
        if value == "" or (isinstance(value, str) and not value.strip()):
            raise ValueError("run_at must not be blank")
        return value

    @field_validator("run_at", mode="after")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("run_at must include a timezone offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def complete_run_id(self) -> "BenchmarkRun":
        if not self.run_id:
            self.run_id = _stable_id(
                "run",
                self.competitor,
                self.task_id,
                self.task_revision,
                self.task_fingerprint,
                self.validator_sha256,
                self.protocol_sha256,
                self.starter_sha256,
                self.candidate_sha256,
                self.product_version,
                self.model,
                self.run_at,
            )
        return self


class BenchmarkImportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    imported_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    persisted_count: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)
    runs: list[BenchmarkRun] = Field(default_factory=list)


class BenchmarkComparisonRow(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    competitor: str
    run_count: int = Field(ge=0)
    task_success_rate: float = Field(ge=0.0, le=1.0)
    average_test_pass_rate: float = Field(ge=0.0, le=1.0)
    average_edit_rounds: float = Field(ge=0.0)
    harmful_action_count: int = Field(ge=0)
    unique_task_count: int = Field(default=0, ge=0)
    total_task_count: int = Field(default=0, ge=0)
    task_coverage_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    covered_task_type_count: int = Field(default=0, ge=0)
    total_task_type_count: int = Field(default=0, ge=0)
    task_type_coverage_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    covered_task_ids: list[str] = Field(default_factory=list)
    compile_evaluated_count: int = Field(default=0, ge=0)
    compile_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    average_latency_ms: float = Field(default=0.0, ge=0.0)
    total_manual_intervention: int = Field(default=0, ge=0)
    average_manual_intervention: float = Field(default=0.0, ge=0.0)
    total_estimated_cost: float = Field(default=0.0, ge=0.0)
    average_estimated_cost: float = Field(default=0.0, ge=0.0)
    harmful_action_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    safe_run_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    duplicate_run_count: int = Field(default=0, ge=0)


__all__ = [
    "BenchmarkComparisonRow",
    "BenchmarkDifficulty",
    "BenchmarkImportSummary",
    "BenchmarkRun",
    "BenchmarkTask",
    "BenchmarkTaskType",
]
