"""Public contracts for one-shot evidence-backed intelligence questions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_rag.models import Conflict, Evidence


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=2000)
    analysis_target: str | None = Field(default=None, max_length=160)
    start_time: datetime | None = None
    end_time: datetime | None = None
    top_k: int = Field(default=8, ge=1, le=20)

    @model_validator(mode="after")
    def validate_window(self) -> "AskRequest":
        if self.start_time and self.end_time and self.start_time > self.end_time:
            raise ValueError("start_time must be before or equal to end_time")
        return self


class AskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ask_id: str
    query_id: str
    answer: str
    answer_mode: Literal["hybrid", "rules_fallback"]
    references: list[Evidence] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    generated_at: datetime


__all__ = ["AskRequest", "AskResponse"]
