"""Single-slot durable Worker for asynchronous Multi-Agent workflows."""

from __future__ import annotations

import os
import socket
import threading
from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from agents.orchestrator import MultiAgentOrchestrator, OrchestrationObserver
from backend.workflow_repository import AsyncWorkflowRepository, WorkflowClaim
from schemas.orchestration import BranchOutcome, SpecialistBranch


@dataclass(frozen=True)
class WorkflowWorkerSettings:
    poll_seconds: float = 1.0
    lease_seconds: int = 30
    heartbeat_seconds: int = 10
    timeout_seconds: int = 300

    @classmethod
    def from_env(cls) -> "WorkflowWorkerSettings":
        settings = cls(
            poll_seconds=float(
                os.getenv("CODERADAR_WORKFLOW_POLL_SECONDS", "1")
            ),
            lease_seconds=int(
                os.getenv("CODERADAR_WORKFLOW_LEASE_SECONDS", "30")
            ),
            heartbeat_seconds=int(
                os.getenv("CODERADAR_WORKFLOW_HEARTBEAT_SECONDS", "10")
            ),
            timeout_seconds=int(
                os.getenv("CODERADAR_WORKFLOW_TIMEOUT_SECONDS", "300")
            ),
        )
        if settings.poll_seconds <= 0:
            raise ValueError("workflow poll interval must be positive")
        if settings.lease_seconds < 3:
            raise ValueError("workflow lease must be at least 3 seconds")
        if not 0 < settings.heartbeat_seconds < settings.lease_seconds:
            raise ValueError("workflow heartbeat must be shorter than its lease")
        if settings.timeout_seconds <= 0:
            raise ValueError("workflow timeout must be positive")
        return settings


class DatabaseWorkflowObserver(OrchestrationObserver):
    def __init__(
        self,
        repository: AsyncWorkflowRepository,
        claim: WorkflowClaim,
        lease_lost: threading.Event,
    ) -> None:
        self.repository = repository
        self.claim = claim
        self.lease_lost = lease_lost

    def branch_started(self, branch: SpecialistBranch) -> None:
        self.repository.branch_started(
            self.claim.workflow_id,
            self.claim.attempt,
            branch,
        )

    def branch_completed(self, outcome: BranchOutcome) -> None:
        self.repository.branch_completed(
            self.claim.workflow_id,
            self.claim.attempt,
            outcome,
        )

    def stage_progress(self, stage: str, progress: int) -> None:
        self.repository.stage_progress(self.claim.workflow_id, progress)

    def stop_reason(self) -> str | None:
        if self.lease_lost.is_set():
            return "worker_lost"
        return self.repository.stop_reason(self.claim.workflow_id)


class WorkflowWorker:
    def __init__(
        self,
        orchestrator: MultiAgentOrchestrator | None,
        repository: AsyncWorkflowRepository | None = None,
        *,
        orchestrator_factory: Callable[[str], MultiAgentOrchestrator] | None = None,
        settings: WorkflowWorkerSettings | None = None,
        worker_id: str | None = None,
    ) -> None:
        if orchestrator is None and orchestrator_factory is None:
            raise ValueError("orchestrator or orchestrator_factory is required")
        self.orchestrator = orchestrator
        self.orchestrator_factory = orchestrator_factory
        self._mode_orchestrators: dict[str, MultiAgentOrchestrator] = {}
        self.repository = repository or AsyncWorkflowRepository()
        self.settings = settings or WorkflowWorkerSettings.from_env()
        self.worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        )
        self._stop = threading.Event()
        self._owns_lease = False

    def _ensure_worker_lease(self) -> bool:
        if self._owns_lease:
            renewed = self.repository.renew_leases(
                self.worker_id,
                workflow_id=None,
                lease_seconds=self.settings.lease_seconds,
            )
            if not renewed:
                self._owns_lease = False
            return renewed
        self._owns_lease = self.repository.acquire_worker_lease(
            self.worker_id,
            lease_seconds=self.settings.lease_seconds,
        )
        return self._owns_lease

    def run_once(self) -> bool:
        if not self._ensure_worker_lease():
            return False
        self.repository.recover_stale()
        claim = self.repository.claim_next(
            self.worker_id,
            lease_seconds=self.settings.lease_seconds,
            timeout_seconds=self.settings.timeout_seconds,
        )
        if claim is None:
            return False

        lease_lost = threading.Event()
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(claim.workflow_id, heartbeat_stop, lease_lost),
            name=f"workflow-heartbeat-{claim.workflow_id}",
            daemon=True,
        )
        heartbeat.start()
        observer = DatabaseWorkflowObserver(self.repository, claim, lease_lost)
        try:
            initial_stop = observer.stop_reason()
            if initial_stop is not None:
                self.repository.finalize_stopped(
                    claim.workflow_id,
                    attempt=claim.attempt,
                    reason=initial_stop,
                )
                return True
            reusable = self.repository.reusable_outcomes(
                claim.workflow_id,
                claim.request,
            )
            selected_orchestrator = self._orchestrator_for(claim.request.analysis_mode)
            result = selected_orchestrator.run_attempt(
                claim.request,
                reusable_outcomes=reusable,
                observer=observer,
            )
            # The queue owns the durable, actor-scoped workflow identifier.
            # Orchestrators still derive their in-memory ID from only the
            # analysis request, so replace it before persistence.
            result = result.model_copy(
                update={
                    "workflow_id": claim.workflow_id,
                    "request_fingerprint": claim.request_fingerprint,
                }
            )
            reason = observer.stop_reason()
            if reason in {"cancelled", "timed_out"}:
                self.repository.finalize_stopped(
                    claim.workflow_id,
                    attempt=claim.attempt,
                    reason=reason,
                )
            elif reason == "worker_lost":
                raise RuntimeError("worker lease was lost during execution")
            else:
                self.repository.finalize_result(result, attempt=claim.attempt)
            return True
        except Exception as exc:
            self.repository.finalize_exception(
                claim.workflow_id,
                attempt=claim.attempt,
                exc=exc,
            )
            return True
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=self.settings.heartbeat_seconds + 1)

    def _orchestrator_for(self, mode: str) -> MultiAgentOrchestrator:
        if self.orchestrator_factory is None:
            assert self.orchestrator is not None
            return self.orchestrator
        if mode not in self._mode_orchestrators:
            self._mode_orchestrators[mode] = self.orchestrator_factory(mode)
        return self._mode_orchestrators[mode]

    def _heartbeat(
        self,
        workflow_id: str,
        stop: threading.Event,
        lease_lost: threading.Event,
    ) -> None:
        while not stop.wait(self.settings.heartbeat_seconds):
            try:
                renewed = self.repository.renew_leases(
                    self.worker_id,
                    workflow_id=workflow_id,
                    lease_seconds=self.settings.lease_seconds,
                )
            except Exception:
                continue
            if not renewed:
                lease_lost.set()
                return

    def run_forever(self) -> None:
        try:
            while not self._stop.is_set():
                processed = self.run_once()
                if not processed:
                    self._stop.wait(self.settings.poll_seconds)
        finally:
            self.close()

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        if self._owns_lease:
            self.repository.release_worker_lease(self.worker_id)
            self._owns_lease = False


__all__ = [
    "DatabaseWorkflowObserver",
    "WorkflowWorker",
    "WorkflowWorkerSettings",
]
