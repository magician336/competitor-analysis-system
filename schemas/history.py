"""User-owned AI Ask and evidence retrieval history contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from mini_rag.models import RAGQuery, RAGResponse
from schemas.ask import AskRequest, AskResponse


class AskHistorySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ask_id: str
    question: str
    analysis_target: str | None = None
    answer_preview: str
    answer_mode: str
    generated_at: datetime


class AskHistoryDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ask_id: str
    request: AskRequest
    response: AskResponse


class EvidenceHistorySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    question: str
    competitor: str | None = None
    result_count: int
    latency_ms: float
    created_at: datetime


class EvidenceHistoryDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    request: RAGQuery
    response: RAGResponse


__all__ = [
    "AskHistoryDetail",
    "AskHistorySummary",
    "EvidenceHistoryDetail",
    "EvidenceHistorySummary",
]
