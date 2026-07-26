"""Query-oriented repository used by the formal phase-three API."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Literal

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from agents.compare_agent import CompareAgent
from backend.config import PROJECT_ROOT
from backend.database import get_database
from backend.models import (
    ApiAuditEventRecord,
    BriefingRecord,
    CapabilitySnapshotRecord,
    CardEvidenceRecord,
    ComparisonMatrixRecord,
    ComparisonSnapshotRecord,
    CompetitorRecord,
    IntelligenceCardRecord,
    SnapshotCardRecord,
    WorkflowRecord,
)
from backend.repositories import AnalysisRepository
from schemas.api import (
    BriefingDetail,
    BriefingSummary,
    CardDetailResponse,
    CardSummary,
    ComparisonCreateRequest,
    ComparisonResponse,
    ComparisonSummary,
    CompetitorCreate,
    CompetitorResponse,
    CompetitorWrite,
    EvidenceDetailResponse,
    Page,
    SnapshotDetailResponse,
    SnapshotSummary,
    WorkflowSummary,
)
from schemas.capability_snapshot import CapabilitySnapshot
from schemas.comparison import CapabilityComparisonMatrix
from schemas.intelligence_card import EvidenceReference, IntelligenceCard


class ApiObjectNotFound(LookupError):
    pass


class ApiConflictError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _page(session: Session, statement: Select, *, page: int, page_size: int):
    total = session.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ) or 0
    rows = list(
        session.scalars(statement.offset((page - 1) * page_size).limit(page_size))
    )
    return rows, int(total)


def _text_equal(column, value: str):
    return func.lower(column) == value.strip().casefold()


class FormalApiRepository:
    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self.session_factory = session_factory or get_database().session_factory
        self.analysis = AnalysisRepository(self.session_factory)

    def list_cards(
        self,
        *,
        page: int,
        page_size: int,
        competitor: str | None = None,
        agent_kind: str | None = None,
        event_type: str | None = None,
        alert_level: str | None = None,
        review_required: bool | None = None,
        evidence_level: str | None = None,
        source_type: str | None = None,
        min_confidence: float | None = None,
        min_priority: int | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        sort_by: Literal["created_at", "priority_score", "confidence_score"] = "created_at",
        order: Literal["asc", "desc"] = "desc",
    ) -> Page[CardSummary]:
        with self.session_factory() as session:
            statement = select(IntelligenceCardRecord)
            filters = []
            if competitor:
                filters.append(_text_equal(IntelligenceCardRecord.competitor, competitor))
            if agent_kind:
                filters.append(IntelligenceCardRecord.agent_kind == agent_kind)
            if event_type:
                filters.append(IntelligenceCardRecord.event_type == event_type)
            if alert_level:
                filters.append(IntelligenceCardRecord.alert_level == alert_level)
            if review_required is not None:
                filters.append(IntelligenceCardRecord.review_required.is_(review_required))
            if min_confidence is not None:
                filters.append(IntelligenceCardRecord.confidence_score >= min_confidence)
            if min_priority is not None:
                filters.append(IntelligenceCardRecord.priority_score >= min_priority)
            if created_from is not None:
                filters.append(IntelligenceCardRecord.created_at >= created_from)
            if created_to is not None:
                filters.append(IntelligenceCardRecord.created_at <= created_to)
            if evidence_level or source_type:
                evidence_filters = [
                    CardEvidenceRecord.card_id == IntelligenceCardRecord.card_id
                ]
                if evidence_level:
                    evidence_filters.append(CardEvidenceRecord.evidence_level == evidence_level)
                if source_type:
                    evidence_filters.append(CardEvidenceRecord.source_type == source_type)
                filters.append(select(CardEvidenceRecord.card_id).where(*evidence_filters).exists())
            if filters:
                statement = statement.where(*filters)
            sort_column = getattr(IntelligenceCardRecord, sort_by)
            direction = sort_column.asc() if order == "asc" else sort_column.desc()
            statement = statement.order_by(direction, IntelligenceCardRecord.card_id.asc())
            records, total = _page(session, statement, page=page, page_size=page_size)
            items = []
            for record in records:
                card = IntelligenceCard.model_validate(record.payload)
                publish_time = max(
                    (
                        evidence.publish_time
                        for evidence in card.evidence
                        if evidence.publish_time is not None
                    ),
                    default=None,
                )
                items.append(
                    CardSummary(
                        card_id=card.card_id,
                        competitor=card.competitor,
                        agent_kind=card.agent_kind,
                        event_type=card.event_type,
                        event_title=card.event_title,
                        summary=card.summary,
                        alert_level=card.alert_level,
                        confidence_score=card.confidence_score,
                        priority_score=card.priority_score,
                        review_required=card.review_required,
                        evidence_count=len(card.evidence),
                        publish_time=publish_time,
                        created_at=card.created_at,
                    )
                )
            return Page[CardSummary].build(items, page=page, page_size=page_size, total=total)

    def card_detail(self, card_id: str) -> CardDetailResponse:
        with self.session_factory() as session:
            record = session.get(IntelligenceCardRecord, card_id)
            if record is None:
                raise ApiObjectNotFound("card not found")
            evidence = list(
                session.scalars(
                    select(CardEvidenceRecord)
                    .where(CardEvidenceRecord.card_id == card_id)
                    .order_by(CardEvidenceRecord.chunk_id)
                )
            )
            return CardDetailResponse(
                card=IntelligenceCard.model_validate(record.payload),
                evidence_links=[EvidenceReference.model_validate(item.payload) for item in evidence],
            )

    def card_evidence(self, card_id: str) -> list[EvidenceReference]:
        return self.card_detail(card_id).evidence_links

    def evidence_detail(self, chunk_id: str) -> EvidenceDetailResponse:
        with self.session_factory() as session:
            records = list(
                session.scalars(
                    select(CardEvidenceRecord)
                    .where(CardEvidenceRecord.chunk_id == chunk_id)
                    .order_by(CardEvidenceRecord.card_id)
                )
            )
            if not records:
                raise ApiObjectNotFound("evidence not found")
            return EvidenceDetailResponse(
                evidence=EvidenceReference.model_validate(records[0].payload),
                card_ids=[item.card_id for item in records],
            )

    def list_snapshots(
        self,
        *,
        page: int,
        page_size: int,
        competitor: str | None = None,
        snapshot_from: date | None = None,
        snapshot_to: date | None = None,
        product_version: str | None = None,
        scoring_version: str | None = None,
        min_coverage: float | None = None,
        min_confidence: float | None = None,
    ) -> Page[SnapshotSummary]:
        with self.session_factory() as session:
            statement = select(CapabilitySnapshotRecord)
            filters = []
            if competitor:
                filters.append(_text_equal(CapabilitySnapshotRecord.competitor, competitor))
            if snapshot_from:
                filters.append(CapabilitySnapshotRecord.snapshot_date >= snapshot_from)
            if snapshot_to:
                filters.append(CapabilitySnapshotRecord.snapshot_date <= snapshot_to)
            if product_version:
                filters.append(CapabilitySnapshotRecord.product_version == product_version)
            if scoring_version:
                filters.append(CapabilitySnapshotRecord.scoring_version == scoring_version)
            if min_coverage is not None:
                filters.append(CapabilitySnapshotRecord.coverage_ratio >= min_coverage)
            if min_confidence is not None:
                filters.append(CapabilitySnapshotRecord.overall_confidence >= min_confidence)
            if filters:
                statement = statement.where(*filters)
            statement = statement.order_by(
                CapabilitySnapshotRecord.snapshot_date.desc(),
                CapabilitySnapshotRecord.created_at.desc(),
                CapabilitySnapshotRecord.snapshot_id.asc(),
            )
            records, total = _page(session, statement, page=page, page_size=page_size)
            items = [
                SnapshotSummary(
                    snapshot_id=item.snapshot_id,
                    competitor=item.competitor,
                    snapshot_date=item.snapshot_date,
                    product_version=item.product_version,
                    scoring_version=item.scoring_version,
                    total_score=item.total_score,
                    overall_confidence=item.overall_confidence,
                    coverage_ratio=item.coverage_ratio,
                    previous_snapshot_id=item.previous_snapshot_id,
                    source_kind=item.source_kind,
                    created_at=item.created_at,
                )
                for item in records
            ]
            return Page[SnapshotSummary].build(items, page=page, page_size=page_size, total=total)

    def snapshot_detail(self, snapshot_id: str) -> SnapshotDetailResponse:
        with self.session_factory() as session:
            record = session.get(CapabilitySnapshotRecord, snapshot_id)
            if record is None:
                raise ApiObjectNotFound("snapshot not found")
            return SnapshotDetailResponse(
                snapshot=CapabilitySnapshot.model_validate(record.payload),
                source_kind=record.source_kind,
                provenance=dict(record.provenance or {}),
            )

    def snapshot_cards(self, snapshot_id: str) -> list[IntelligenceCard]:
        with self.session_factory() as session:
            if session.get(CapabilitySnapshotRecord, snapshot_id) is None:
                raise ApiObjectNotFound("snapshot not found")
            records = list(
                session.scalars(
                    select(IntelligenceCardRecord)
                    .join(SnapshotCardRecord, SnapshotCardRecord.card_id == IntelligenceCardRecord.card_id)
                    .where(SnapshotCardRecord.snapshot_id == snapshot_id)
                    .order_by(IntelligenceCardRecord.created_at.desc(), IntelligenceCardRecord.card_id)
                )
            )
            return [IntelligenceCard.model_validate(item.payload) for item in records]

    def create_comparison(self, request: ComparisonCreateRequest) -> ComparisonResponse:
        parsed = ComparisonCreateRequest.model_validate(request)
        with self.session_factory() as session:
            current_records = {
                item.snapshot_id: item
                for item in session.scalars(
                    select(CapabilitySnapshotRecord).where(
                        CapabilitySnapshotRecord.snapshot_id.in_(parsed.snapshot_ids)
                    )
                )
            }
            previous_records = {
                item.snapshot_id: item
                for item in session.scalars(
                    select(CapabilitySnapshotRecord).where(
                        CapabilitySnapshotRecord.snapshot_id.in_(parsed.previous_snapshot_ids)
                    )
                )
            }
        missing = set(parsed.snapshot_ids) - set(current_records)
        missing_previous = set(parsed.previous_snapshot_ids) - set(previous_records)
        if missing or missing_previous:
            raise ApiObjectNotFound("snapshot not found: " + ", ".join(sorted(missing | missing_previous)))
        snapshots = [CapabilitySnapshot.model_validate(current_records[item].payload) for item in parsed.snapshot_ids]
        previous = [CapabilitySnapshot.model_validate(previous_records[item].payload) for item in parsed.previous_snapshot_ids]
        windows = {(item.window_start, item.window_end) for item in snapshots}
        if len(windows) != 1:
            raise ApiConflictError("comparison snapshots must use the same analysis window")
        versions = {item.scoring_version for item in snapshots}
        if len(versions) != 1:
            raise ApiConflictError("comparison snapshots must use the same scoring version")
        canonical = json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        comparison_id = "comparison_" + fingerprint[:20]
        with self.session_factory() as session:
            existing = session.scalar(
                select(ComparisonMatrixRecord).where(
                    ComparisonMatrixRecord.request_fingerprint == fingerprint
                )
            )
            if existing is not None:
                return self._comparison_response(existing)
        try:
            matrix = CompareAgent().compare_snapshots(
                snapshots,
                baseline_product=parsed.baseline_product,
                previous_snapshots=previous,
                rules=parsed.rules,
            )
        except ValueError as exc:
            raise ApiConflictError(str(exc)) from exc
        official_ready = all(item.rank_eligible for item in matrix.products)
        provenance = {
            "snapshot_ids": parsed.snapshot_ids,
            "previous_snapshot_ids": parsed.previous_snapshot_ids,
            "baseline_product": matrix.baseline_product,
            "baseline_source_kind": next(
                current_records[item.snapshot_id].source_kind
                for item in snapshots
                if item.competitor.casefold() == matrix.baseline_product.casefold()
            ),
            "minimum_rank_coverage": parsed.rules.minimum_rank_coverage,
            "official_ranking_ready": official_ready,
            "planning_only": not official_ready,
        }
        now = utc_now()
        try:
            with self.analysis.transaction() as store:
                record = ComparisonMatrixRecord(
                    comparison_id=comparison_id,
                    request_fingerprint=fingerprint,
                    baseline_product=matrix.baseline_product,
                    scoring_version=matrix.scoring_version,
                    comparison_date=matrix.comparison_date,
                    official_ranking_ready=official_ready,
                    payload=matrix.model_dump(mode="json"),
                    provenance=provenance,
                    created_at=now,
                )
                store.session.add(record)
                store.session.flush()
                for snapshot_id in parsed.snapshot_ids:
                    store.session.add(
                        ComparisonSnapshotRecord(
                            comparison_id=comparison_id,
                            snapshot_id=snapshot_id,
                        )
                    )
        except IntegrityError:
            with self.session_factory() as session:
                existing = session.scalar(
                    select(ComparisonMatrixRecord).where(
                        ComparisonMatrixRecord.request_fingerprint == fingerprint
                    )
                )
                if existing is None:
                    raise
                return self._comparison_response(existing)
        return ComparisonResponse(
            comparison_id=comparison_id,
            request_fingerprint=fingerprint,
            official_ranking_ready=official_ready,
            matrix=matrix,
            provenance=provenance,
            created_at=now,
        )

    @staticmethod
    def _comparison_response(record: ComparisonMatrixRecord) -> ComparisonResponse:
        return ComparisonResponse(
            comparison_id=record.comparison_id,
            request_fingerprint=record.request_fingerprint,
            official_ranking_ready=record.official_ranking_ready,
            matrix=CapabilityComparisonMatrix.model_validate(record.payload),
            provenance=dict(record.provenance),
            created_at=record.created_at,
        )

    def comparison(self, comparison_id: str) -> ComparisonResponse:
        with self.session_factory() as session:
            record = session.get(ComparisonMatrixRecord, comparison_id)
            if record is None:
                raise ApiObjectNotFound("comparison not found")
            return self._comparison_response(record)

    def latest_comparison(self) -> ComparisonResponse:
        with self.session_factory() as session:
            record = session.scalar(
                select(ComparisonMatrixRecord).order_by(
                    ComparisonMatrixRecord.created_at.desc(),
                    ComparisonMatrixRecord.comparison_id.asc(),
                )
            )
            if record is None:
                raise ApiObjectNotFound("comparison not found")
            return self._comparison_response(record)

    def list_comparisons(self, *, page: int, page_size: int) -> Page[ComparisonSummary]:
        with self.session_factory() as session:
            statement = select(ComparisonMatrixRecord).order_by(
                ComparisonMatrixRecord.created_at.desc(),
                ComparisonMatrixRecord.comparison_id.asc(),
            )
            records, total = _page(session, statement, page=page, page_size=page_size)
            items = []
            for record in records:
                snapshot_ids = list(
                    session.scalars(
                        select(ComparisonSnapshotRecord.snapshot_id)
                        .where(ComparisonSnapshotRecord.comparison_id == record.comparison_id)
                        .order_by(ComparisonSnapshotRecord.snapshot_id)
                    )
                )
                items.append(
                    ComparisonSummary(
                        comparison_id=record.comparison_id,
                        baseline_product=record.baseline_product,
                        scoring_version=record.scoring_version,
                        comparison_date=record.comparison_date,
                        official_ranking_ready=record.official_ranking_ready,
                        snapshot_ids=snapshot_ids,
                        created_at=record.created_at,
                    )
                )
            return Page[ComparisonSummary].build(items, page=page, page_size=page_size, total=total)

    def list_briefings(
        self,
        *,
        page: int,
        page_size: int,
        user_id: str | None = None,
        machine_only: bool = False,
        competitor: str | None = None,
        snapshot_id: str | None = None,
        workflow_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> Page[BriefingSummary]:
        with self.session_factory() as session:
            statement = select(BriefingRecord)
            filters = []
            if user_id is not None:
                filters.append(
                    BriefingRecord.workflow_id.in_(
                        select(WorkflowRecord.workflow_id).where(
                            WorkflowRecord.user_id == user_id
                        )
                    )
                )
            elif machine_only:
                filters.append(
                    or_(
                        BriefingRecord.workflow_id.is_(None),
                        BriefingRecord.workflow_id.in_(
                            select(WorkflowRecord.workflow_id).where(
                                WorkflowRecord.user_id.is_(None)
                            )
                        ),
                    )
                )
            if competitor:
                filters.append(_text_equal(BriefingRecord.competitor, competitor))
            if snapshot_id:
                filters.append(BriefingRecord.snapshot_id == snapshot_id)
            if workflow_id:
                filters.append(BriefingRecord.workflow_id == workflow_id)
            if created_from:
                filters.append(BriefingRecord.created_at >= created_from)
            if created_to:
                filters.append(BriefingRecord.created_at <= created_to)
            if filters:
                statement = statement.where(*filters)
            statement = statement.order_by(BriefingRecord.created_at.desc(), BriefingRecord.briefing_id.asc())
            records, total = _page(session, statement, page=page, page_size=page_size)
            items = [
                BriefingSummary(
                    briefing_id=item.briefing_id,
                    competitor=item.competitor,
                    snapshot_id=item.snapshot_id,
                    workflow_id=item.workflow_id,
                    created_at=item.created_at,
                )
                for item in records
            ]
            return Page[BriefingSummary].build(items, page=page, page_size=page_size, total=total)

    def briefing(
        self,
        briefing_id: str,
        *,
        user_id: str | None = None,
        machine_only: bool = False,
    ) -> BriefingDetail:
        with self.session_factory() as session:
            record = session.get(BriefingRecord, briefing_id)
            owner_id = (
                session.scalar(
                    select(WorkflowRecord.user_id).where(
                        WorkflowRecord.workflow_id == record.workflow_id
                    )
                )
                if record is not None and record.workflow_id is not None
                else None
            )
            if (
                record is None
                or (user_id is not None and owner_id != user_id)
                or (machine_only and owner_id is not None)
            ):
                raise ApiObjectNotFound("briefing not found")
            return BriefingDetail(
                briefing_id=record.briefing_id,
                competitor=record.competitor,
                snapshot_id=record.snapshot_id,
                workflow_id=record.workflow_id,
                created_at=record.created_at,
                markdown=record.markdown,
            )

    def list_workflows(
        self,
        *,
        page: int,
        page_size: int,
        user_id: str | None = None,
        machine_only: bool = False,
        competitor: str | None = None,
        workflow_status: str | None = None,
        analysis_mode: str | None = None,
        correlation_id: str | None = None,
        submitted_from: datetime | None = None,
        submitted_to: datetime | None = None,
    ) -> Page[WorkflowSummary]:
        with self.session_factory() as session:
            statement = select(WorkflowRecord)
            filters = []
            if user_id is not None:
                filters.append(WorkflowRecord.user_id == user_id)
            elif machine_only:
                filters.append(WorkflowRecord.user_id.is_(None))
            if competitor:
                filters.append(_text_equal(WorkflowRecord.competitor, competitor))
            if workflow_status:
                filters.append(WorkflowRecord.status == workflow_status)
            if analysis_mode:
                filters.append(
                    WorkflowRecord.request_payload["analysis_mode"].as_string()
                    == analysis_mode
                )
            if correlation_id:
                filters.append(WorkflowRecord.request_payload["correlation_id"].as_string() == correlation_id)
            if submitted_from:
                filters.append(WorkflowRecord.submitted_at >= submitted_from)
            if submitted_to:
                filters.append(WorkflowRecord.submitted_at <= submitted_to)
            if filters:
                statement = statement.where(*filters)
            statement = statement.order_by(WorkflowRecord.submitted_at.desc(), WorkflowRecord.workflow_id.asc())
            records, total = _page(session, statement, page=page, page_size=page_size)
            items = []
            for item in records:
                request_payload = dict(item.request_payload or {})
                items.append(
                    WorkflowSummary(
                        workflow_id=item.workflow_id,
                        competitor=item.competitor,
                        analysis_mode=request_payload.get("analysis_mode", "rules"),
                        status=item.status,
                        progress=item.progress,
                        attempt_count=item.attempt_count,
                        max_attempts=item.max_attempts,
                        partial_failure=item.partial_failure,
                        correlation_id=request_payload.get("correlation_id"),
                        submitted_at=item.submitted_at,
                        completed_at=item.completed_at,
                        updated_at=item.updated_at,
                        last_error=item.last_error,
                    )
                )
            return Page[WorkflowSummary].build(items, page=page, page_size=page_size, total=total)

    @staticmethod
    def _competitor(record: CompetitorRecord) -> CompetitorResponse:
        return CompetitorResponse(
            id=record.competitor_id,
            name=record.name,
            aliases=list(record.aliases),
            sources=dict(record.sources),
            enabled=record.enabled,
            config_version=record.config_version,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def list_competitors(self, *, enabled_only: bool = False) -> list[CompetitorResponse]:
        with self.session_factory() as session:
            statement = select(CompetitorRecord)
            if enabled_only:
                statement = statement.where(CompetitorRecord.enabled.is_(True))
            statement = statement.order_by(CompetitorRecord.competitor_id)
            return [self._competitor(item) for item in session.scalars(statement)]

    def create_competitor(self, request: CompetitorCreate) -> CompetitorResponse:
        parsed = CompetitorCreate.model_validate(request)
        now = utc_now()
        with self.analysis.transaction() as store:
            duplicate = store.session.scalar(
                select(CompetitorRecord).where(
                    or_(
                        CompetitorRecord.competitor_id == parsed.id,
                        _text_equal(CompetitorRecord.name, parsed.name),
                    )
                )
            )
            if duplicate is not None:
                raise ApiConflictError("competitor id or name already exists")
            record = CompetitorRecord(
                competitor_id=parsed.id,
                name=parsed.name,
                aliases=parsed.aliases,
                sources=parsed.sources,
                enabled=parsed.enabled,
                config_version=1,
                created_at=now,
                updated_at=now,
            )
            store.session.add(record)
            store.session.flush()
            response = self._competitor(record)
        return response

    def update_competitor(self, competitor_id: str, request: CompetitorWrite) -> CompetitorResponse:
        parsed = CompetitorWrite.model_validate(request)
        with self.analysis.transaction() as store:
            record = store.session.get(CompetitorRecord, competitor_id)
            if record is None:
                raise ApiObjectNotFound("competitor not found")
            duplicate = store.session.scalar(
                select(CompetitorRecord).where(
                    and_(
                        CompetitorRecord.competitor_id != competitor_id,
                        _text_equal(CompetitorRecord.name, parsed.name),
                    )
                )
            )
            if duplicate is not None:
                raise ApiConflictError("competitor name already exists")
            record.name = parsed.name
            record.aliases = parsed.aliases
            record.sources = parsed.sources
            record.enabled = parsed.enabled
            record.config_version += 1
            record.updated_at = utc_now()
            store.session.flush()
            response = self._competitor(record)
        return response

    def disable_competitor(self, competitor_id: str) -> None:
        with self.analysis.transaction() as store:
            record = store.session.get(CompetitorRecord, competitor_id)
            if record is None:
                raise ApiObjectNotFound("competitor not found")
            record.enabled = False
            record.config_version += 1
            record.updated_at = utc_now()

    def record_audit(
        self,
        *,
        event_id: str,
        request_id: str,
        action: str,
        method: str,
        path: str,
        resource_type: str | None,
        resource_id: str | None,
        status_code: int,
        duration_ms: float,
        client_ip_hash: str | None,
    ) -> None:
        with self.analysis.transaction() as store:
            store.session.add(
                ApiAuditEventRecord(
                    event_id=event_id,
                    request_id=request_id,
                    action=action,
                    method=method,
                    path=path[:320],
                    resource_type=resource_type,
                    resource_id=resource_id,
                    status_code=status_code,
                    duration_ms=max(duration_ms, 0.0),
                    api_key_id="primary",
                    client_ip_hash=client_ip_hash,
                    created_at=utc_now(),
                )
            )


_repository: FormalApiRepository | None = None


def get_formal_api_repository() -> FormalApiRepository:
    global _repository
    if _repository is None:
        _repository = FormalApiRepository()
    return _repository


def set_formal_api_repository(repository: FormalApiRepository | None) -> None:
    global _repository
    _repository = repository


__all__ = [
    "ApiConflictError",
    "ApiObjectNotFound",
    "FormalApiRepository",
    "get_formal_api_repository",
    "set_formal_api_repository",
]
