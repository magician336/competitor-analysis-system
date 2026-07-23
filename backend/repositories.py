"""Transactional repositories for CodeRadar persistence."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.database import get_database
from backend.models import (
    AgentTraceRecord,
    ArtifactImportRecord,
    BriefingRecord,
    CapabilitySnapshotRecord,
    CardEvidenceRecord,
    CompetitorRecord,
    IntelligenceCardRecord,
    SnapshotCardRecord,
    WorkflowBranchRecord,
    WorkflowRecord,
)
from schemas.capability_snapshot import CapabilitySnapshot
from schemas.intelligence_card import (
    AgentExecutionTrace,
    AgentRunResult,
    EvidenceReference,
    IntelligenceCard,
)
from schemas.orchestration import MultiAgentAnalysisResult


_write_lock = threading.RLock()


def _json_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        raise TypeError("payload must be a Pydantic model or mapping")
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def briefing_id_for(competitor: str, snapshot_id: str | None, markdown: str) -> str:
    canonical = json.dumps(
        [competitor.strip().casefold(), snapshot_id, markdown],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "briefing_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


class AnalysisStore:
    """Low-level operations scoped to one caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_competitors(
        self,
        competitors: Sequence[Mapping[str, Any]],
        *,
        config_version: int,
    ) -> int:
        for item in competitors:
            competitor_id = str(item["id"]).strip()
            name = str(item["name"]).strip()
            if not competitor_id or not name:
                raise ValueError("competitor id and name must not be blank")
            record = self.session.get(CompetitorRecord, competitor_id)
            if record is None:
                record = CompetitorRecord(competitor_id=competitor_id, name=name)
                self.session.add(record)
            record.name = name
            record.aliases = list(item.get("aliases", []))
            record.sources = dict(item.get("sources", {}))
            record.enabled = bool(item.get("enabled", True))
            record.config_version = config_version
            record.updated_at = datetime.now(timezone.utc)
        return len(competitors)

    def upsert_cards(self, cards: Sequence[IntelligenceCard]) -> int:
        for raw_card in cards:
            card = IntelligenceCard.model_validate(raw_card)
            payload = card.model_dump(mode="json")
            record = self.session.get(IntelligenceCardRecord, card.card_id)
            if record is None:
                record = IntelligenceCardRecord(card_id=card.card_id)
                self.session.add(record)
            record.schema_version = card.schema_version
            record.competitor = card.competitor
            record.agent_kind = card.agent_kind.value
            record.event_type = card.event_type.value
            record.alert_level = card.alert_level.value
            record.confidence_score = card.confidence_score
            record.priority_score = card.priority_score
            record.review_required = card.review_required
            record.created_at = card.created_at
            record.payload = payload
            self.session.flush()
            self.session.execute(
                delete(CardEvidenceRecord).where(
                    CardEvidenceRecord.card_id == card.card_id
                )
            )
            for evidence in card.evidence:
                self.session.add(
                    CardEvidenceRecord(
                        card_id=card.card_id,
                        chunk_id=evidence.chunk_id,
                        citation_id=evidence.citation_id,
                        document_id=evidence.document_id,
                        version_id=evidence.version_id,
                        competitor=evidence.competitor,
                        title=evidence.title,
                        url=evidence.url,
                        source_type=evidence.source_type.value,
                        evidence_level=evidence.evidence_level.value,
                        publish_time=evidence.publish_time,
                        payload=evidence.model_dump(mode="json"),
                    )
                )
        return len(cards)

    def upsert_snapshot(
        self,
        raw_snapshot: CapabilitySnapshot,
        *,
        preserve_existing: bool = False,
        source_kind: str = "agent_evidence",
        provenance: Mapping[str, Any] | None = None,
    ) -> CapabilitySnapshot:
        snapshot = CapabilitySnapshot.model_validate(raw_snapshot)
        record = self.session.get(CapabilitySnapshotRecord, snapshot.snapshot_id)
        if record is not None and preserve_existing:
            return CapabilitySnapshot.model_validate(record.payload)
        if record is None:
            record = CapabilitySnapshotRecord(snapshot_id=snapshot.snapshot_id)
            self.session.add(record)
        record.competitor = snapshot.competitor
        record.snapshot_date = snapshot.snapshot_date
        record.product_version = snapshot.product_version
        record.scoring_version = snapshot.scoring_version
        record.window_start = snapshot.window_start
        record.window_end = snapshot.window_end
        record.total_score = snapshot.total_score
        record.overall_confidence = snapshot.overall_confidence
        record.coverage_ratio = snapshot.coverage_ratio
        record.previous_snapshot_id = snapshot.previous_snapshot_id
        record.source_kind = source_kind
        record.provenance = _json_payload(provenance or {})
        record.payload = snapshot.model_dump(mode="json")
        self.session.flush()
        self.session.execute(
            delete(SnapshotCardRecord).where(
                SnapshotCardRecord.snapshot_id == snapshot.snapshot_id
            )
        )
        existing_card_ids = set(
            self.session.scalars(
                select(IntelligenceCardRecord.card_id).where(
                    IntelligenceCardRecord.card_id.in_(snapshot.input_card_ids)
                )
            )
        )
        missing = set(snapshot.input_card_ids) - existing_card_ids
        if missing:
            raise ValueError(
                "snapshot references unknown card(s): " + ", ".join(sorted(missing))
            )
        for card_id in snapshot.input_card_ids:
            self.session.add(
                SnapshotCardRecord(snapshot_id=snapshot.snapshot_id, card_id=card_id)
            )
        return snapshot

    def upsert_workflow_result(self, result: MultiAgentAnalysisResult) -> None:
        validated = MultiAgentAnalysisResult.model_validate(result)
        self.upsert_cards(validated.cards)
        if validated.snapshot is not None:
            self.upsert_snapshot(validated.snapshot, preserve_existing=True)
        payload = validated.model_dump(mode="json")
        record = self.session.get(WorkflowRecord, validated.workflow_id)
        if record is None:
            record = WorkflowRecord(workflow_id=validated.workflow_id)
            self.session.add(record)
        record.request_fingerprint = validated.request_fingerprint
        record.competitor = validated.request.competitor
        record.status = validated.status.value
        record.partial_failure = validated.partial_failure
        record.started_at = validated.started_at
        record.completed_at = validated.completed_at
        record.duration_ms = validated.duration_ms
        record.payload = payload
        self.session.flush()
        for branch, outcome in validated.branch_outcomes.items():
            branch_name = branch.value
            branch_record = self.session.get(
                WorkflowBranchRecord,
                (validated.workflow_id, branch_name),
            )
            if branch_record is None:
                branch_record = WorkflowBranchRecord(
                    workflow_id=validated.workflow_id,
                    branch=branch_name,
                )
                self.session.add(branch_record)
            branch_record.status = outcome.status.value
            branch_record.duration_ms = outcome.duration_ms
            branch_record.rag_query_id = (
                outcome.result.rag_query_id if outcome.result is not None else None
            )
            branch_record.error = (
                outcome.error.model_dump(mode="json")
                if outcome.error is not None
                else None
            )
            branch_record.payload = outcome.model_dump(mode="json")
            if outcome.result is not None and outcome.result.trace is not None:
                self.upsert_trace(
                    outcome.result.trace,
                    workflow_id=validated.workflow_id,
                    branch=branch_name,
                )
        if validated.briefing is not None:
            self.upsert_briefing(
                competitor=validated.request.competitor,
                markdown=validated.briefing,
                snapshot_id=(
                    validated.snapshot.snapshot_id
                    if validated.snapshot is not None
                    else None
                ),
                workflow_id=validated.workflow_id,
                created_at=validated.completed_at,
                payload={
                    "workflow_id": validated.workflow_id,
                    "snapshot_id": (
                        validated.snapshot.snapshot_id
                        if validated.snapshot is not None
                        else None
                    ),
                },
            )

    def upsert_workflow_summary(self, payload: Mapping[str, Any]) -> None:
        workflow_id = str(payload["workflow_id"])
        record = self.session.get(WorkflowRecord, workflow_id)
        if record is None:
            record = WorkflowRecord(workflow_id=workflow_id)
            self.session.add(record)
        record.request_fingerprint = None
        record.competitor = str(payload["competitor"])
        record.status = str(payload["status"])
        record.partial_failure = record.status == "partial_failure"
        record.started_at = None
        record.completed_at = None
        record.duration_ms = None
        record.payload = _json_payload(payload)
        self.session.flush()
        for branch, status in dict(payload.get("branches", {})).items():
            branch_record = self.session.get(
                WorkflowBranchRecord, (workflow_id, str(branch))
            )
            if branch_record is None:
                branch_record = WorkflowBranchRecord(
                    workflow_id=workflow_id,
                    branch=str(branch),
                )
                self.session.add(branch_record)
            branch_record.status = str(status)
            branch_record.duration_ms = None
            branch_record.rag_query_id = None
            branch_record.error = None
            branch_record.payload = {"branch": str(branch), "status": str(status)}

    def upsert_trace(
        self,
        raw_trace: AgentExecutionTrace,
        *,
        workflow_id: str | None,
        branch: str | None,
    ) -> None:
        trace = AgentExecutionTrace.model_validate(raw_trace)
        record = self.session.get(AgentTraceRecord, trace.trace_id)
        if record is None:
            record = AgentTraceRecord(trace_id=trace.trace_id)
            self.session.add(record)
        record.workflow_id = workflow_id
        record.branch = branch
        record.agent_kind = trace.agent_kind.value
        record.duration_ms = trace.duration_ms
        record.llm_used = trace.llm_used
        record.fallback_used = trace.fallback_used
        record.model_name = trace.model_name
        record.llm_call_count = trace.llm_call_count
        record.input_tokens = trace.input_tokens
        record.output_tokens = trace.output_tokens
        record.total_tokens = trace.total_tokens
        record.payload = trace.model_dump(mode="json")

    def upsert_trace_summary(self, payload: Mapping[str, Any]) -> None:
        workflow_id = str(payload["workflow_id"])
        branch = str(payload["branch"])
        trace = AgentExecutionTrace.model_validate(payload["trace"])
        branch_record = self.session.get(
            WorkflowBranchRecord,
            (workflow_id, branch),
        )
        if branch_record is None:
            raise ValueError(
                f"trace references missing workflow branch: {workflow_id}/{branch}"
            )
        branch_record.status = str(payload["status"])
        branch_record.duration_ms = float(payload["duration_ms"])
        branch_record.rag_query_id = payload.get("rag_query_id")
        branch_record.error = payload.get("error")
        branch_record.payload = _json_payload(payload)
        self.upsert_trace(trace, workflow_id=workflow_id, branch=branch)

    def upsert_briefing(
        self,
        *,
        competitor: str,
        markdown: str,
        snapshot_id: str | None,
        workflow_id: str | None = None,
        created_at: datetime | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        normalized = markdown.strip()
        if not normalized:
            raise ValueError("briefing markdown must not be blank")
        briefing_id = briefing_id_for(competitor, snapshot_id, normalized)
        record = self.session.get(BriefingRecord, briefing_id)
        if record is None:
            record = BriefingRecord(briefing_id=briefing_id)
            self.session.add(record)
        record.competitor = competitor.strip()
        record.snapshot_id = snapshot_id
        record.workflow_id = workflow_id
        record.markdown = normalized
        record.created_at = created_at or datetime.now(timezone.utc)
        record.payload = _json_payload(payload or {})
        return briefing_id

    def add_artifact_import(
        self,
        *,
        source_root: str,
        source_sha256: str,
        artifact_version: str,
        counts: Mapping[str, int],
        manifest: Mapping[str, Any],
    ) -> None:
        self.session.add(
            ArtifactImportRecord(
                source_root=source_root,
                source_sha256=source_sha256,
                artifact_version=artifact_version,
                counts=dict(counts),
                manifest=_json_payload(manifest),
            )
        )


class AnalysisRepository:
    """Application repository with one transaction per public mutation."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self.session_factory = session_factory or get_database().session_factory

    @contextmanager
    def transaction(self) -> Iterator[AnalysisStore]:
        session = self.session_factory()
        try:
            # SQLite has one writer at a time.  Serializing in-process writes
            # also makes identical concurrent Agent requests deterministic.
            with _write_lock:
                with session.begin():
                    yield AnalysisStore(session)
        finally:
            session.close()

    def save_agent_result(self, result: AgentRunResult) -> None:
        validated = AgentRunResult.model_validate(result)
        with self.transaction() as store:
            store.upsert_cards(validated.cards)
            if validated.trace is not None:
                store.upsert_trace(validated.trace, workflow_id=None, branch=None)

    def save_workflow_result(self, result: MultiAgentAnalysisResult) -> None:
        with self.transaction() as store:
            store.upsert_workflow_result(result)

    def save_snapshot(
        self,
        snapshot: CapabilitySnapshot,
        *,
        preserve_existing: bool = False,
        source_kind: str = "agent_evidence",
        provenance: Mapping[str, Any] | None = None,
    ) -> CapabilitySnapshot:
        with self.transaction() as store:
            return store.upsert_snapshot(
                snapshot,
                preserve_existing=preserve_existing,
                source_kind=source_kind,
                provenance=provenance,
            )

    def save_briefing(
        self,
        *,
        competitor: str,
        markdown: str,
        snapshot_id: str | None,
        workflow_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        with self.transaction() as store:
            return store.upsert_briefing(
                competitor=competitor,
                markdown=markdown,
                snapshot_id=snapshot_id,
                workflow_id=workflow_id,
                payload=payload,
            )

    def save_snapshot_and_briefing(
        self,
        *,
        snapshot: CapabilitySnapshot,
        competitor: str,
        markdown: str,
        workflow_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[CapabilitySnapshot, str]:
        with self.transaction() as store:
            stored = store.upsert_snapshot(snapshot, preserve_existing=True)
            briefing_id = store.upsert_briefing(
                competitor=competitor,
                markdown=markdown,
                snapshot_id=stored.snapshot_id,
                workflow_id=workflow_id,
                payload=payload,
            )
            return stored, briefing_id

    def list_cards(self, competitor: str | None = None) -> list[IntelligenceCard]:
        with self.session_factory() as session:
            statement = select(IntelligenceCardRecord)
            if competitor:
                statement = statement.where(
                    func.lower(IntelligenceCardRecord.competitor)
                    == competitor.strip().casefold()
                )
            statement = statement.order_by(IntelligenceCardRecord.created_at.desc())
            return [
                IntelligenceCard.model_validate(record.payload)
                for record in session.scalars(statement)
            ]

    def get_card(self, card_id: str) -> IntelligenceCard | None:
        with self.session_factory() as session:
            record = session.get(IntelligenceCardRecord, card_id)
            return (
                IntelligenceCard.model_validate(record.payload)
                if record is not None
                else None
            )

    def get_card_evidence(self, card_id: str) -> list[EvidenceReference]:
        with self.session_factory() as session:
            statement = (
                select(CardEvidenceRecord)
                .where(CardEvidenceRecord.card_id == card_id)
                .order_by(CardEvidenceRecord.chunk_id)
            )
            return [
                EvidenceReference.model_validate(record.payload)
                for record in session.scalars(statement)
            ]

    def list_snapshots(
        self,
        competitor: str | None = None,
    ) -> list[CapabilitySnapshot]:
        with self.session_factory() as session:
            statement = select(CapabilitySnapshotRecord)
            if competitor:
                statement = statement.where(
                    func.lower(CapabilitySnapshotRecord.competitor)
                    == competitor.strip().casefold()
                )
            statement = statement.order_by(
                CapabilitySnapshotRecord.snapshot_date.desc(),
                CapabilitySnapshotRecord.created_at.desc(),
                CapabilitySnapshotRecord.snapshot_id.desc(),
            )
            return [
                CapabilitySnapshot.model_validate(record.payload)
                for record in session.scalars(statement)
            ]

    def get_snapshot(self, snapshot_id: str) -> CapabilitySnapshot | None:
        with self.session_factory() as session:
            record = session.get(CapabilitySnapshotRecord, snapshot_id)
            return (
                CapabilitySnapshot.model_validate(record.payload)
                if record is not None
                else None
            )

    def latest_snapshot(self, competitor: str) -> CapabilitySnapshot | None:
        snapshots = self.list_snapshots(competitor)
        return snapshots[0] if snapshots else None

    def artifact_import_exists(self, source_sha256: str) -> bool:
        with self.session_factory() as session:
            return (
                session.scalar(
                    select(ArtifactImportRecord.import_id).where(
                        ArtifactImportRecord.source_sha256 == source_sha256
                    )
                )
                is not None
            )


class CompetitorRepository:
    """Competitor configuration access; SQLite is authoritative after seeding."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self.session_factory = session_factory or get_database().session_factory

    def upsert(self, competitors: Sequence[Mapping[str, Any]], *, version: int) -> int:
        repository = AnalysisRepository(self.session_factory)
        with repository.transaction() as store:
            return store.upsert_competitors(competitors, config_version=version)

    def list(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            statement = select(CompetitorRecord)
            if enabled_only:
                statement = statement.where(CompetitorRecord.enabled.is_(True))
            statement = statement.order_by(CompetitorRecord.competitor_id)
            return [
                {
                    "id": record.competitor_id,
                    "name": record.name,
                    "aliases": list(record.aliases),
                    "sources": dict(record.sources),
                    "enabled": record.enabled,
                    "config_version": record.config_version,
                }
                for record in session.scalars(statement)
            ]


class CardRepository:
    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._repository = AnalysisRepository(session_factory)

    def upsert(self, cards: Sequence[IntelligenceCard]) -> int:
        with self._repository.transaction() as store:
            return store.upsert_cards(cards)

    def list(self, competitor: str | None = None) -> list[IntelligenceCard]:
        return self._repository.list_cards(competitor)

    def get(self, card_id: str) -> IntelligenceCard | None:
        return self._repository.get_card(card_id)

    def evidence(self, card_id: str) -> list[EvidenceReference]:
        return self._repository.get_card_evidence(card_id)


class SnapshotRepository:
    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._repository = AnalysisRepository(session_factory)

    def upsert(
        self,
        snapshot: CapabilitySnapshot,
        *,
        preserve_existing: bool = False,
    ) -> CapabilitySnapshot:
        return self._repository.save_snapshot(
            snapshot,
            preserve_existing=preserve_existing,
        )

    def list(self, competitor: str | None = None) -> list[CapabilitySnapshot]:
        return self._repository.list_snapshots(competitor)

    def get(self, snapshot_id: str) -> CapabilitySnapshot | None:
        return self._repository.get_snapshot(snapshot_id)

    def latest(self, competitor: str) -> CapabilitySnapshot | None:
        return self._repository.latest_snapshot(competitor)


class WorkflowRepository:
    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._repository = AnalysisRepository(session_factory)

    def upsert(self, result: MultiAgentAnalysisResult) -> None:
        self._repository.save_workflow_result(result)

    def get_payload(self, workflow_id: str) -> dict[str, Any] | None:
        with self._repository.session_factory() as session:
            record = session.get(WorkflowRecord, workflow_id)
            return dict(record.payload) if record is not None else None


class TraceRepository:
    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._repository = AnalysisRepository(session_factory)

    def get(self, trace_id: str) -> AgentExecutionTrace | None:
        with self._repository.session_factory() as session:
            record = session.get(AgentTraceRecord, trace_id)
            return (
                AgentExecutionTrace.model_validate(record.payload)
                if record is not None
                else None
            )


class BriefingRepository:
    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._repository = AnalysisRepository(session_factory)

    def save(
        self,
        *,
        competitor: str,
        markdown: str,
        snapshot_id: str | None,
        workflow_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        return self._repository.save_briefing(
            competitor=competitor,
            markdown=markdown,
            snapshot_id=snapshot_id,
            workflow_id=workflow_id,
            payload=payload,
        )

    def list(self, competitor: str | None = None) -> list[dict[str, Any]]:
        with self._repository.session_factory() as session:
            statement = select(BriefingRecord)
            if competitor:
                statement = statement.where(
                    func.lower(BriefingRecord.competitor)
                    == competitor.strip().casefold()
                )
            statement = statement.order_by(BriefingRecord.created_at.desc())
            return [
                {
                    "briefing_id": record.briefing_id,
                    "competitor": record.competitor,
                    "snapshot_id": record.snapshot_id,
                    "workflow_id": record.workflow_id,
                    "markdown": record.markdown,
                    "created_at": record.created_at,
                    "payload": dict(record.payload),
                }
                for record in session.scalars(statement)
            ]


_lock = threading.RLock()
_repository: AnalysisRepository | None = None


def get_analysis_repository() -> AnalysisRepository:
    global _repository
    with _lock:
        if _repository is None:
            _repository = AnalysisRepository()
        return _repository


def set_analysis_repository(repository: AnalysisRepository | None) -> None:
    global _repository
    with _lock:
        _repository = repository


__all__ = [
    "AnalysisRepository",
    "AnalysisStore",
    "BriefingRepository",
    "CardRepository",
    "CompetitorRepository",
    "SnapshotRepository",
    "TraceRepository",
    "WorkflowRepository",
    "briefing_id_for",
    "get_analysis_repository",
    "set_analysis_repository",
]
