"""Durable SQLite queue operations for asynchronous Multi-Agent workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from backend.database import get_database
from backend.models import (
    BriefingRecord,
    WorkflowAttemptRecord,
    WorkflowBranchAttemptRecord,
    WorkflowBranchRecord,
    WorkflowRecord,
    WorkflowWorkerLeaseRecord,
)
from backend.repositories import AnalysisRepository
from schemas.orchestration import (
    BranchExecutionStatus,
    BranchOutcome,
    MultiAgentAnalysisRequest,
    MultiAgentAnalysisResult,
    SpecialistBranch,
)
from schemas.workflow import (
    TERMINAL_WORKFLOW_STATUSES,
    WorkflowActionResponse,
    WorkflowBranchResponse,
    WorkflowBranchStatus,
    WorkflowStatus,
    WorkflowStatusResponse,
    WorkflowSubmissionResponse,
)


WORKER_LEASE_NAME = "async-workflow-singleton"
RETRYABLE_STATUSES = {
    WorkflowStatus.CANCELLED,
    WorkflowStatus.FAILED,
    WorkflowStatus.PARTIAL_FAILURE,
    WorkflowStatus.TIMED_OUT,
}


class WorkflowNotFoundError(LookupError):
    pass


class WorkflowConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowClaim:
    workflow_id: str
    request_fingerprint: str
    request: MultiAgentAnalysisRequest
    attempt: int
    deadline_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _error_payload(
    error_type: str,
    message: str,
    *,
    stage: str,
    retryable: bool,
) -> dict[str, Any]:
    return {
        "error_type": error_type,
        "message": " ".join(message.split())[:1_000],
        "stage": stage,
        "retryable": retryable,
    }


class AsyncWorkflowRepository:
    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self.session_factory = session_factory or get_database().session_factory
        self.analysis = AnalysisRepository(self.session_factory)

    def submit(
        self,
        request: MultiAgentAnalysisRequest,
        *,
        max_attempts: int = 2,
        user_id: str | None = None,
    ) -> WorkflowSubmissionResponse:
        parsed = MultiAgentAnalysisRequest.model_validate(request)
        owner_scope = user_id or "__api__"
        request_fingerprint = hashlib.sha256(
            f"{owner_scope}\0{parsed.fingerprint}".encode("utf-8")
        ).hexdigest()
        workflow_id = (
            "workflow_"
            + hashlib.sha256(
                f"{owner_scope}\0{parsed.stable_workflow_id}".encode("utf-8")
            ).hexdigest()[:24]
        )
        now = _now()
        with self.analysis.transaction() as store:
            statement = (
                sqlite_insert(WorkflowRecord)
                .values(
                    workflow_id=workflow_id,
                    user_id=user_id,
                    request_fingerprint=request_fingerprint,
                    competitor=parsed.competitor,
                    status=WorkflowStatus.QUEUED.value,
                    partial_failure=False,
                    started_at=None,
                    completed_at=None,
                    duration_ms=None,
                    payload={},
                    request_payload=parsed.model_dump(mode="json"),
                    progress=0,
                    cancel_requested=False,
                    attempt_count=0,
                    max_attempts=max_attempts,
                    submitted_at=now,
                    updated_at=now,
                    available_at=now,
                    claimed_at=None,
                    lease_expires_at=None,
                    worker_id=None,
                    deadline_at=None,
                    last_error=None,
                )
                .on_conflict_do_nothing(index_elements=["request_fingerprint"])
            )
            inserted = store.session.execute(statement).rowcount == 1
            record = store.session.scalar(
                select(WorkflowRecord).where(
                    WorkflowRecord.request_fingerprint == request_fingerprint
                )
            )
            if record is None:
                raise RuntimeError("workflow idempotent insert did not return a record")
            for branch in parsed.branches:
                store.session.execute(
                    sqlite_insert(WorkflowBranchRecord)
                    .values(
                        workflow_id=record.workflow_id,
                        branch=branch.value,
                        status=WorkflowBranchStatus.PENDING.value,
                        duration_ms=None,
                        rag_query_id=None,
                        error=None,
                        payload={},
                        progress=0,
                        last_attempt=0,
                        started_at=None,
                        completed_at=None,
                        trace_id=None,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["workflow_id", "branch"]
                    )
                )
            return WorkflowSubmissionResponse(
                workflow_id=record.workflow_id,
                status=WorkflowStatus(record.status),
                deduplicated=not inserted,
                status_url=f"/api/workflows/{record.workflow_id}",
                submitted_at=_aware(record.submitted_at),
            )

    def get(
        self,
        workflow_id: str,
        *,
        user_id: str | None = None,
        machine_only: bool = False,
    ) -> WorkflowStatusResponse:
        with self.session_factory() as session:
            record = session.get(WorkflowRecord, workflow_id)
            if (
                record is None
                or (user_id is not None and record.user_id != user_id)
                or (machine_only and record.user_id is not None)
            ):
                raise WorkflowNotFoundError(workflow_id)
            branches = list(
                session.scalars(
                    select(WorkflowBranchRecord)
                    .where(WorkflowBranchRecord.workflow_id == workflow_id)
                    .order_by(WorkflowBranchRecord.branch)
                )
            )
            result = self._validated_result(record)
            card_ids = (
                [card.card_id for card in result.cards] if result is not None else []
            )
            snapshot_id = (
                result.snapshot.snapshot_id
                if result is not None and result.snapshot is not None
                else None
            )
            briefing_id = session.scalar(
                select(BriefingRecord.briefing_id)
                .where(BriefingRecord.workflow_id == workflow_id)
                .order_by(BriefingRecord.created_at.desc())
                .limit(1)
            )
            return WorkflowStatusResponse(
                workflow_id=record.workflow_id,
                request_fingerprint=record.request_fingerprint,
                competitor=record.competitor,
                analysis_mode=(record.request_payload or {}).get("analysis_mode", "rules"),
                status=WorkflowStatus(record.status),
                progress=record.progress,
                attempt_count=record.attempt_count,
                max_attempts=record.max_attempts,
                cancel_requested=record.cancel_requested,
                submitted_at=_aware(record.submitted_at),
                started_at=_aware(record.started_at),
                completed_at=_aware(record.completed_at),
                deadline_at=_aware(record.deadline_at),
                updated_at=_aware(record.updated_at),
                branches=[self._branch_response(item) for item in branches],
                card_ids=card_ids,
                snapshot_id=snapshot_id,
                briefing_id=briefing_id,
                last_error=record.last_error,
                result=result,
            )

    @staticmethod
    def _validated_result(record: WorkflowRecord) -> MultiAgentAnalysisResult | None:
        if WorkflowStatus(record.status) not in TERMINAL_WORKFLOW_STATUSES:
            return None
        try:
            return MultiAgentAnalysisResult.model_validate(record.payload)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _branch_response(record: WorkflowBranchRecord) -> WorkflowBranchResponse:
        card_ids: list[str] = []
        react_used = False
        react_iterations = 0
        tool_call_count = 0
        if record.payload:
            try:
                outcome = BranchOutcome.model_validate(record.payload)
                if outcome.result is not None:
                    card_ids = [card.card_id for card in outcome.result.cards]
                    if outcome.result.trace is not None:
                        react_used = outcome.result.trace.react_used
                        react_iterations = outcome.result.trace.react_iterations
                        tool_call_count = outcome.result.trace.tool_call_count
            except (TypeError, ValueError):
                pass
        return WorkflowBranchResponse(
            branch=SpecialistBranch(record.branch),
            status=WorkflowBranchStatus(record.status),
            progress=record.progress,
            last_attempt=record.last_attempt,
            started_at=_aware(record.started_at),
            completed_at=_aware(record.completed_at),
            duration_ms=record.duration_ms,
            rag_query_id=record.rag_query_id,
            card_ids=card_ids,
            trace_id=record.trace_id,
            react_used=react_used,
            react_iterations=react_iterations,
            tool_call_count=tool_call_count,
            error=record.error,
        )

    def cancel(
        self,
        workflow_id: str,
        *,
        user_id: str | None = None,
        machine_only: bool = False,
    ) -> WorkflowActionResponse:
        now = _now()
        with self.analysis.transaction() as store:
            record = store.session.get(WorkflowRecord, workflow_id)
            if (
                record is None
                or (user_id is not None and record.user_id != user_id)
                or (machine_only and record.user_id is not None)
            ):
                raise WorkflowNotFoundError(workflow_id)
            status = WorkflowStatus(record.status)
            if status == WorkflowStatus.CANCELLED:
                return self._action(record, "workflow is already cancelled")
            if status in TERMINAL_WORKFLOW_STATUSES:
                raise WorkflowConflictError(
                    f"workflow in terminal state {status.value!r} cannot be cancelled"
                )
            record.cancel_requested = True
            record.updated_at = now
            if status == WorkflowStatus.QUEUED:
                record.status = WorkflowStatus.CANCELLED.value
                record.progress = 100
                record.completed_at = now
                record.available_at = None
                for branch in self._branches(store.session, workflow_id):
                    if branch.status == WorkflowBranchStatus.PENDING.value:
                        branch.status = WorkflowBranchStatus.CANCELLED.value
                        branch.progress = 100
                        branch.completed_at = now
            else:
                record.status = WorkflowStatus.CANCELLING.value
            return self._action(record, "cancellation accepted")

    def retry(
        self,
        workflow_id: str,
        *,
        user_id: str | None = None,
        machine_only: bool = False,
    ) -> WorkflowActionResponse:
        now = _now()
        with self.analysis.transaction() as store:
            record = store.session.get(WorkflowRecord, workflow_id)
            if (
                record is None
                or (user_id is not None and record.user_id != user_id)
                or (machine_only and record.user_id is not None)
            ):
                raise WorkflowNotFoundError(workflow_id)
            status = WorkflowStatus(record.status)
            if status not in RETRYABLE_STATUSES:
                raise WorkflowConflictError(
                    f"workflow in state {status.value!r} cannot be retried"
                )
            if record.request_payload is None:
                raise WorkflowConflictError("legacy workflow has no retryable request")
            if record.attempt_count >= record.max_attempts:
                raise WorkflowConflictError("workflow retry budget is exhausted")
            branches = self._branches(store.session, workflow_id)
            reusable = 0
            for branch in branches:
                if branch.status == WorkflowBranchStatus.SUCCESS.value:
                    reusable += 1
                    continue
                branch.status = WorkflowBranchStatus.PENDING.value
                branch.progress = 0
                branch.started_at = None
                branch.completed_at = None
                branch.duration_ms = None
                branch.rag_query_id = None
                branch.trace_id = None
                branch.error = None
            record.status = WorkflowStatus.QUEUED.value
            record.progress = 5 + round(75 * reusable / max(1, len(branches)))
            record.partial_failure = False
            record.payload = {}
            record.cancel_requested = False
            record.completed_at = None
            record.available_at = now
            record.claimed_at = None
            record.lease_expires_at = None
            record.worker_id = None
            record.deadline_at = None
            record.last_error = None
            record.updated_at = now
            return self._action(record, "retry queued")

    @staticmethod
    def _action(record: WorkflowRecord, message: str) -> WorkflowActionResponse:
        return WorkflowActionResponse(
            workflow_id=record.workflow_id,
            status=WorkflowStatus(record.status),
            attempt_count=record.attempt_count,
            message=message,
        )

    def acquire_worker_lease(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
    ) -> bool:
        now = _now()
        expires = now + timedelta(seconds=lease_seconds)
        with self.analysis.transaction() as store:
            statement = sqlite_insert(WorkflowWorkerLeaseRecord).values(
                lease_name=WORKER_LEASE_NAME,
                worker_id=worker_id,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=expires,
            )
            statement = statement.on_conflict_do_update(
                index_elements=["lease_name"],
                set_={
                    "worker_id": worker_id,
                    "acquired_at": now,
                    "heartbeat_at": now,
                    "expires_at": expires,
                },
                where=or_(
                    WorkflowWorkerLeaseRecord.expires_at <= now,
                    WorkflowWorkerLeaseRecord.worker_id == worker_id,
                ),
            )
            return store.session.execute(statement).rowcount == 1

    def renew_leases(
        self,
        worker_id: str,
        *,
        workflow_id: str | None,
        lease_seconds: int,
    ) -> bool:
        now = _now()
        expires = now + timedelta(seconds=lease_seconds)
        with self.analysis.transaction() as store:
            worker_updated = store.session.execute(
                update(WorkflowWorkerLeaseRecord)
                .where(
                    WorkflowWorkerLeaseRecord.lease_name == WORKER_LEASE_NAME,
                    WorkflowWorkerLeaseRecord.worker_id == worker_id,
                )
                .values(heartbeat_at=now, expires_at=expires)
            ).rowcount
            if not worker_updated:
                return False
            if workflow_id is not None:
                job_updated = store.session.execute(
                    update(WorkflowRecord)
                    .where(
                        WorkflowRecord.workflow_id == workflow_id,
                        WorkflowRecord.worker_id == worker_id,
                        WorkflowRecord.status.in_(
                            [
                                WorkflowStatus.RUNNING.value,
                                WorkflowStatus.CANCELLING.value,
                            ]
                        ),
                    )
                    .values(lease_expires_at=expires, updated_at=now)
                ).rowcount
                return bool(job_updated)
            return True

    def release_worker_lease(self, worker_id: str) -> None:
        with self.analysis.transaction() as store:
            store.session.execute(
                delete(WorkflowWorkerLeaseRecord).where(
                    WorkflowWorkerLeaseRecord.lease_name == WORKER_LEASE_NAME,
                    WorkflowWorkerLeaseRecord.worker_id == worker_id,
                )
            )

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        timeout_seconds: int,
    ) -> WorkflowClaim | None:
        now = _now()
        with self.analysis.transaction() as store:
            record = store.session.scalar(
                select(WorkflowRecord)
                .where(
                    WorkflowRecord.status == WorkflowStatus.QUEUED.value,
                    WorkflowRecord.request_payload.is_not(None),
                    or_(
                        WorkflowRecord.available_at.is_(None),
                        WorkflowRecord.available_at <= now,
                    ),
                )
                .order_by(WorkflowRecord.submitted_at, WorkflowRecord.workflow_id)
                .limit(1)
            )
            if record is None:
                return None
            if record.attempt_count >= record.max_attempts:
                record.status = WorkflowStatus.FAILED.value
                record.progress = 100
                record.completed_at = now
                record.updated_at = now
                record.last_error = _error_payload(
                    "RetryBudgetExceeded",
                    "workflow attempt budget is exhausted",
                    stage="claim",
                    retryable=False,
                )
                return None
            updated = store.session.execute(
                update(WorkflowRecord)
                .where(
                    WorkflowRecord.workflow_id == record.workflow_id,
                    WorkflowRecord.status == WorkflowStatus.QUEUED.value,
                )
                .values(status=WorkflowStatus.RUNNING.value)
            ).rowcount
            if not updated:
                return None
            attempt = record.attempt_count + 1
            deadline = now + timedelta(seconds=timeout_seconds)
            record.status = WorkflowStatus.RUNNING.value
            record.progress = max(5, record.progress)
            record.attempt_count = attempt
            record.started_at = record.started_at or now
            record.claimed_at = now
            record.updated_at = now
            record.worker_id = worker_id
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.deadline_at = deadline
            record.completed_at = None
            record.cancel_requested = False
            store.session.add(
                WorkflowAttemptRecord(
                    workflow_id=record.workflow_id,
                    attempt=attempt,
                    status=WorkflowStatus.RUNNING.value,
                    worker_id=worker_id,
                    started_at=now,
                    metrics={},
                )
            )
            request = MultiAgentAnalysisRequest.model_validate(record.request_payload)
            for branch in request.branches:
                current = store.session.get(
                    WorkflowBranchRecord,
                    (record.workflow_id, branch.value),
                )
                reusable = (
                    current is not None
                    and current.status == WorkflowBranchStatus.SUCCESS.value
                )
                store.session.add(
                    WorkflowBranchAttemptRecord(
                        workflow_id=record.workflow_id,
                        attempt=attempt,
                        branch=branch.value,
                        status=(
                            WorkflowBranchStatus.SKIPPED.value
                            if reusable
                            else WorkflowBranchStatus.PENDING.value
                        ),
                        completed_at=now if reusable else None,
                        payload=dict(current.payload) if reusable else {},
                    )
                )
            return WorkflowClaim(
                workflow_id=record.workflow_id,
                request_fingerprint=record.request_fingerprint or request.fingerprint,
                request=request,
                attempt=attempt,
                deadline_at=deadline,
            )

    def reusable_outcomes(
        self,
        workflow_id: str,
        request: MultiAgentAnalysisRequest,
    ) -> dict[SpecialistBranch, BranchOutcome]:
        with self.session_factory() as session:
            records = list(
                session.scalars(
                    select(WorkflowBranchRecord).where(
                        WorkflowBranchRecord.workflow_id == workflow_id,
                        WorkflowBranchRecord.status
                        == WorkflowBranchStatus.SUCCESS.value,
                    )
                )
            )
            allowed = set(request.branches)
            outcomes: dict[SpecialistBranch, BranchOutcome] = {}
            for record in records:
                branch = SpecialistBranch(record.branch)
                if branch in allowed:
                    outcome = BranchOutcome.model_validate(record.payload)
                    if outcome.status == BranchExecutionStatus.SUCCESS:
                        outcomes[branch] = outcome
            return outcomes

    def branch_started(
        self,
        workflow_id: str,
        attempt: int,
        branch: SpecialistBranch,
    ) -> None:
        now = _now()
        with self.analysis.transaction() as store:
            current = store.session.get(
                WorkflowBranchRecord, (workflow_id, branch.value)
            )
            attempted = store.session.get(
                WorkflowBranchAttemptRecord,
                (workflow_id, attempt, branch.value),
            )
            if current is None or attempted is None:
                raise RuntimeError("workflow branch state is missing")
            current.status = WorkflowBranchStatus.RUNNING.value
            current.progress = 5
            current.last_attempt = attempt
            current.started_at = now
            current.completed_at = None
            current.error = None
            attempted.status = WorkflowBranchStatus.RUNNING.value
            attempted.started_at = now
            workflow = store.session.get(WorkflowRecord, workflow_id)
            if workflow is not None:
                workflow.updated_at = now

    def branch_completed(
        self,
        workflow_id: str,
        attempt: int,
        outcome: BranchOutcome,
    ) -> None:
        validated = BranchOutcome.model_validate(outcome)
        now = _now()
        branch_status = (
            WorkflowBranchStatus.SUCCESS
            if validated.status == BranchExecutionStatus.SUCCESS
            else WorkflowBranchStatus.FAILED
        )
        with self.analysis.transaction() as store:
            current = store.session.get(
                WorkflowBranchRecord,
                (workflow_id, validated.branch.value),
            )
            attempted = store.session.get(
                WorkflowBranchAttemptRecord,
                (workflow_id, attempt, validated.branch.value),
            )
            if current is None or attempted is None:
                raise RuntimeError("workflow branch state is missing")
            trace_id = None
            rag_query_id = None
            error = (
                validated.error.model_dump(mode="json")
                if validated.error is not None
                else None
            )
            if validated.result is not None:
                rag_query_id = validated.result.rag_query_id
                store.upsert_cards(validated.result.cards)
                if validated.result.trace is not None:
                    trace_id = validated.result.trace.trace_id
                    store.upsert_trace(
                        validated.result.trace,
                        workflow_id=workflow_id,
                        branch=validated.branch.value,
                    )
            payload = validated.model_dump(mode="json")
            current.status = branch_status.value
            current.progress = 100
            current.last_attempt = attempt
            current.completed_at = now
            current.duration_ms = validated.duration_ms
            current.rag_query_id = rag_query_id
            current.trace_id = trace_id
            current.error = error
            current.payload = payload
            attempted.status = branch_status.value
            attempted.completed_at = now
            attempted.duration_ms = validated.duration_ms
            attempted.rag_query_id = rag_query_id
            attempted.trace_id = trace_id
            attempted.error = error
            attempted.payload = payload
            store.session.flush()
            workflow = store.session.get(WorkflowRecord, workflow_id)
            if workflow is not None:
                branch_count = store.session.scalar(
                    select(func.count())
                    .select_from(WorkflowBranchRecord)
                    .where(WorkflowBranchRecord.workflow_id == workflow_id)
                ) or 1
                completed = store.session.scalar(
                    select(func.count())
                    .select_from(WorkflowBranchRecord)
                    .where(
                        WorkflowBranchRecord.workflow_id == workflow_id,
                        WorkflowBranchRecord.status.in_(
                            [
                                WorkflowBranchStatus.SUCCESS.value,
                                WorkflowBranchStatus.FAILED.value,
                                WorkflowBranchStatus.CANCELLED.value,
                                WorkflowBranchStatus.SKIPPED.value,
                            ]
                        ),
                    )
                ) or 0
                workflow.progress = min(80, 5 + round(75 * completed / branch_count))
                workflow.updated_at = now

    def stage_progress(self, workflow_id: str, progress: int) -> None:
        now = _now()
        with self.analysis.transaction() as store:
            record = store.session.get(WorkflowRecord, workflow_id)
            if record is not None and record.status in {
                WorkflowStatus.RUNNING.value,
                WorkflowStatus.CANCELLING.value,
            }:
                record.progress = max(record.progress, min(95, progress))
                record.updated_at = now

    def stop_reason(self, workflow_id: str) -> str | None:
        now = _now()
        with self.analysis.transaction() as store:
            record = store.session.get(WorkflowRecord, workflow_id)
            if record is None:
                return "cancelled"
            deadline = _aware(record.deadline_at)
            if deadline is not None and now >= deadline:
                record.cancel_requested = True
                record.status = WorkflowStatus.CANCELLING.value
                record.updated_at = now
                record.last_error = _error_payload(
                    "WorkflowTimeout",
                    "workflow exceeded its execution deadline",
                    stage="timeout",
                    retryable=True,
                )
                return "timed_out"
            if record.cancel_requested:
                return (
                    "timed_out"
                    if record.last_error
                    and record.last_error.get("stage") == "timeout"
                    else "cancelled"
                )
            return None

    def finalize_result(
        self,
        result: MultiAgentAnalysisResult,
        *,
        attempt: int,
    ) -> None:
        validated = MultiAgentAnalysisResult.model_validate(result)
        now = _now()
        with self.analysis.transaction() as store:
            store.upsert_workflow_result(validated)
            record = store.session.get(WorkflowRecord, validated.workflow_id)
            attempted = store.session.get(
                WorkflowAttemptRecord, (validated.workflow_id, attempt)
            )
            if record is None or attempted is None:
                raise RuntimeError("workflow attempt disappeared before finalization")
            record.progress = 100
            record.cancel_requested = False
            record.updated_at = now
            record.worker_id = None
            record.lease_expires_at = None
            record.deadline_at = None
            record.available_at = None
            record.claimed_at = None
            record.last_error = self._result_error(validated)
            attempted.status = validated.status.value
            attempted.completed_at = now
            attempted.duration_ms = self._elapsed_ms(attempted.started_at, now)
            attempted.termination_reason = validated.status.value
            attempted.error = record.last_error
            attempted.metrics = self._attempt_metrics(
                store.session,
                validated.workflow_id,
                attempt,
            )

    @staticmethod
    def _result_error(result: MultiAgentAnalysisResult) -> dict[str, Any] | None:
        failed = [
            outcome.error.model_dump(mode="json")
            for outcome in result.branch_outcomes.values()
            if outcome.error is not None
        ]
        if failed:
            return {"stage": "branches", "errors": failed}
        if result.partial_failure:
            return {
                "stage": "downstream",
                "error_type": "DownstreamFailure",
                "message": "; ".join(result.warnings)[-1_000:],
                "retryable": True,
            }
        return None

    @staticmethod
    def _attempt_metrics(
        session: Session,
        workflow_id: str,
        attempt: int,
    ) -> dict[str, int]:
        traces = []
        rows = list(
            session.scalars(
                select(WorkflowBranchAttemptRecord).where(
                    WorkflowBranchAttemptRecord.workflow_id == workflow_id,
                    WorkflowBranchAttemptRecord.attempt == attempt,
                    WorkflowBranchAttemptRecord.status
                    != WorkflowBranchStatus.SKIPPED.value,
                )
            )
        )
        for row in rows:
            try:
                outcome = BranchOutcome.model_validate(row.payload)
            except (TypeError, ValueError):
                continue
            if outcome.result is not None and outcome.result.trace is not None:
                traces.append(outcome.result.trace)
        return {
            "llm_call_count": sum(trace.llm_call_count for trace in traces),
            "input_tokens": sum(trace.input_tokens for trace in traces),
            "output_tokens": sum(trace.output_tokens for trace in traces),
            "total_tokens": sum(trace.total_tokens for trace in traces),
        }

    def finalize_stopped(
        self,
        workflow_id: str,
        *,
        attempt: int,
        reason: str,
    ) -> None:
        now = _now()
        status = (
            WorkflowStatus.TIMED_OUT
            if reason == "timed_out"
            else WorkflowStatus.CANCELLED
        )
        error = _error_payload(
            "WorkflowTimeout" if status == WorkflowStatus.TIMED_OUT else "Cancelled",
            "workflow timed out" if status == WorkflowStatus.TIMED_OUT else "workflow cancelled",
            stage="timeout" if status == WorkflowStatus.TIMED_OUT else "cancellation",
            retryable=True,
        )
        with self.analysis.transaction() as store:
            record = store.session.get(WorkflowRecord, workflow_id)
            attempted = store.session.get(
                WorkflowAttemptRecord, (workflow_id, attempt)
            )
            if record is None or attempted is None:
                raise RuntimeError("workflow attempt disappeared before stop")
            record.status = status.value
            record.progress = 100
            record.completed_at = now
            record.updated_at = now
            record.worker_id = None
            record.lease_expires_at = None
            record.deadline_at = None
            record.last_error = error
            for branch in self._branches(store.session, workflow_id):
                if branch.status in {
                    WorkflowBranchStatus.PENDING.value,
                    WorkflowBranchStatus.RUNNING.value,
                }:
                    branch.status = WorkflowBranchStatus.CANCELLED.value
                    branch.progress = 100
                    branch.completed_at = now
            attempted.status = status.value
            attempted.completed_at = now
            attempted.duration_ms = self._elapsed_ms(attempted.started_at, now)
            attempted.termination_reason = reason
            attempted.error = error
            attempted.metrics = self._attempt_metrics(
                store.session,
                workflow_id,
                attempt,
            )

    def finalize_exception(
        self,
        workflow_id: str,
        *,
        attempt: int,
        exc: Exception,
    ) -> None:
        now = _now()
        error = _error_payload(
            type(exc).__name__,
            str(exc) or "workflow execution failed",
            stage="worker",
            retryable=isinstance(exc, (ConnectionError, TimeoutError)),
        )
        with self.analysis.transaction() as store:
            record = store.session.get(WorkflowRecord, workflow_id)
            attempted = store.session.get(
                WorkflowAttemptRecord, (workflow_id, attempt)
            )
            if record is None or attempted is None:
                return
            record.status = WorkflowStatus.FAILED.value
            record.progress = 100
            record.completed_at = now
            record.updated_at = now
            record.worker_id = None
            record.lease_expires_at = None
            record.deadline_at = None
            record.last_error = error
            attempted.status = WorkflowStatus.FAILED.value
            attempted.completed_at = now
            attempted.duration_ms = self._elapsed_ms(attempted.started_at, now)
            attempted.termination_reason = "worker_error"
            attempted.error = error
            attempted.metrics = self._attempt_metrics(
                store.session,
                workflow_id,
                attempt,
            )

    def recover_stale(self) -> int:
        now = _now()
        recovered = 0
        with self.analysis.transaction() as store:
            records = list(
                store.session.scalars(
                    select(WorkflowRecord).where(
                        WorkflowRecord.status.in_(
                            [
                                WorkflowStatus.RUNNING.value,
                                WorkflowStatus.CANCELLING.value,
                            ]
                        ),
                        WorkflowRecord.lease_expires_at.is_not(None),
                        WorkflowRecord.lease_expires_at <= now,
                    )
                )
            )
            for record in records:
                recovered += 1
                attempted = store.session.get(
                    WorkflowAttemptRecord,
                    (record.workflow_id, record.attempt_count),
                )
                error = _error_payload(
                    "WorkerLeaseExpired",
                    "worker lease expired before workflow completion",
                    stage="worker_recovery",
                    retryable=True,
                )
                if attempted is not None:
                    attempted.status = WorkflowStatus.FAILED.value
                    attempted.completed_at = now
                    attempted.termination_reason = "worker_lost"
                    attempted.error = error
                if record.status == WorkflowStatus.CANCELLING.value:
                    final_status = (
                        WorkflowStatus.TIMED_OUT
                        if record.last_error
                        and record.last_error.get("stage") == "timeout"
                        else WorkflowStatus.CANCELLED
                    )
                    record.status = final_status.value
                    record.completed_at = now
                    record.progress = 100
                elif record.attempt_count < record.max_attempts:
                    record.status = WorkflowStatus.QUEUED.value
                    record.available_at = now
                    for branch in self._branches(store.session, record.workflow_id):
                        if branch.status == WorkflowBranchStatus.RUNNING.value:
                            branch.status = WorkflowBranchStatus.PENDING.value
                            branch.progress = 0
                            branch.started_at = None
                            branch.completed_at = None
                else:
                    record.status = WorkflowStatus.FAILED.value
                    record.completed_at = now
                    record.progress = 100
                record.worker_id = None
                record.lease_expires_at = None
                record.deadline_at = None
                record.updated_at = now
                record.last_error = error
        return recovered

    @staticmethod
    def _branches(session: Session, workflow_id: str) -> list[WorkflowBranchRecord]:
        return list(
            session.scalars(
                select(WorkflowBranchRecord).where(
                    WorkflowBranchRecord.workflow_id == workflow_id
                )
            )
        )

    @staticmethod
    def _elapsed_ms(started_at: datetime, completed_at: datetime) -> float:
        started = _aware(started_at)
        assert started is not None
        return max(0.0, (completed_at - started).total_seconds() * 1_000)


__all__ = [
    "AsyncWorkflowRepository",
    "WorkflowClaim",
    "WorkflowConflictError",
    "WorkflowNotFoundError",
]
