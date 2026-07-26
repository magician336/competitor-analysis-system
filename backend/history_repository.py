"""Persistence boundary for user-owned Ask and evidence retrieval history."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.database import get_database
from backend.models import (
    AskHistoryRecord,
    EvidenceHistoryRecord,
    MachineQueryTraceRecord,
)
from mini_rag.models import RAGQuery, RAGResponse
from schemas.api import Page
from schemas.ask import AskRequest, AskResponse
from schemas.history import (
    AskHistoryDetail,
    AskHistorySummary,
    EvidenceHistoryDetail,
    EvidenceHistorySummary,
)


class HistoryNotFoundError(LookupError):
    pass


class HistoryRepository:
    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self.session_factory = session_factory or get_database().session_factory

    def save_ask(
        self,
        user_id: str,
        request: AskRequest,
        response: AskResponse,
    ) -> None:
        with self.session_factory.begin() as session:
            session.add(
                AskHistoryRecord(
                    ask_id=response.ask_id,
                    user_id=user_id,
                    question=request.question,
                    analysis_target=request.analysis_target,
                    request_payload=request.model_dump(mode="json"),
                    response_payload=response.model_dump(mode="json"),
                    created_at=response.generated_at,
                )
            )

    def list_asks(
        self, user_id: str, *, page: int, page_size: int
    ) -> Page[AskHistorySummary]:
        with self.session_factory() as session:
            statement = (
                select(AskHistoryRecord)
                .where(AskHistoryRecord.user_id == user_id)
                .order_by(
                    AskHistoryRecord.created_at.desc(),
                    AskHistoryRecord.ask_id.asc(),
                )
            )
            records, total = self._page(
                session, statement, page=page, page_size=page_size
            )
            items = []
            for record in records:
                response = AskResponse.model_validate(record.response_payload)
                preview = " ".join(response.answer.split())
                items.append(
                    AskHistorySummary(
                        ask_id=record.ask_id,
                        question=record.question,
                        analysis_target=record.analysis_target,
                        answer_preview=preview[:160],
                        answer_mode=response.answer_mode,
                        generated_at=response.generated_at,
                    )
                )
            return Page[AskHistorySummary].build(
                items, page=page, page_size=page_size, total=total
            )

    def ask(self, user_id: str, ask_id: str) -> AskHistoryDetail:
        with self.session_factory() as session:
            record = session.scalar(
                select(AskHistoryRecord).where(
                    AskHistoryRecord.ask_id == ask_id,
                    AskHistoryRecord.user_id == user_id,
                )
            )
            if record is None:
                raise HistoryNotFoundError("ask history not found")
            return AskHistoryDetail(
                ask_id=record.ask_id,
                request=AskRequest.model_validate(record.request_payload),
                response=AskResponse.model_validate(record.response_payload),
            )

    def save_evidence(
        self,
        user_id: str,
        request: RAGQuery,
        response: RAGResponse,
    ) -> None:
        latency_ms = float(response.retrieval_trace.latency_ms or 0.0)
        with self.session_factory.begin() as session:
            session.add(
                EvidenceHistoryRecord(
                    query_id=response.query_id,
                    user_id=user_id,
                    question=request.question,
                    competitor=request.competitor,
                    request_payload=request.model_dump(mode="json"),
                    response_payload=response.model_dump(mode="json"),
                    result_count=len(response.evidence),
                    latency_ms=latency_ms,
                    created_at=datetime.now(timezone.utc),
                )
            )

    def list_evidence(
        self, user_id: str, *, page: int, page_size: int
    ) -> Page[EvidenceHistorySummary]:
        with self.session_factory() as session:
            statement = (
                select(EvidenceHistoryRecord)
                .where(EvidenceHistoryRecord.user_id == user_id)
                .order_by(
                    EvidenceHistoryRecord.created_at.desc(),
                    EvidenceHistoryRecord.query_id.asc(),
                )
            )
            records, total = self._page(
                session, statement, page=page, page_size=page_size
            )
            items = [
                EvidenceHistorySummary(
                    query_id=record.query_id,
                    question=record.question,
                    competitor=record.competitor,
                    result_count=record.result_count,
                    latency_ms=record.latency_ms,
                    created_at=record.created_at,
                )
                for record in records
            ]
            return Page[EvidenceHistorySummary].build(
                items, page=page, page_size=page_size, total=total
            )

    def evidence(self, user_id: str, query_id: str) -> EvidenceHistoryDetail:
        with self.session_factory() as session:
            record = session.scalar(
                select(EvidenceHistoryRecord).where(
                    EvidenceHistoryRecord.query_id == query_id,
                    EvidenceHistoryRecord.user_id == user_id,
                )
            )
            if record is None:
                raise HistoryNotFoundError("evidence history not found")
            return EvidenceHistoryDetail(
                query_id=record.query_id,
                request=RAGQuery.model_validate(record.request_payload),
                response=RAGResponse.model_validate(record.response_payload),
            )

    def mark_machine_query(self, query_id: str) -> None:
        with self.session_factory.begin() as session:
            if session.get(MachineQueryTraceRecord, query_id) is None:
                session.add(MachineQueryTraceRecord(query_id=query_id))

    def is_machine_query(self, query_id: str) -> bool:
        with self.session_factory() as session:
            return session.get(MachineQueryTraceRecord, query_id) is not None

    @staticmethod
    def _page(session: Session, statement, *, page: int, page_size: int):
        total = session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        rows = list(
            session.scalars(
                statement.offset((page - 1) * page_size).limit(page_size)
            )
        )
        return rows, int(total)


_repository: HistoryRepository | None = None


def get_history_repository() -> HistoryRepository:
    global _repository
    if _repository is None:
        _repository = HistoryRepository()
    return _repository


__all__ = [
    "HistoryNotFoundError",
    "HistoryRepository",
    "get_history_repository",
]
