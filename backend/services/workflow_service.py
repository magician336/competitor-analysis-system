"""Application service for asynchronous Workflow HTTP operations."""

from __future__ import annotations

import threading

from backend.workflow_repository import AsyncWorkflowRepository
from schemas.workflow import (
    WorkflowActionResponse,
    WorkflowStatusResponse,
    WorkflowSubmissionResponse,
    WorkflowSubmitRequest,
)


class WorkflowService:
    def __init__(self, repository: AsyncWorkflowRepository | None = None) -> None:
        self.repository = repository or AsyncWorkflowRepository()

    def submit(
        self, request: WorkflowSubmitRequest, *, user_id: str | None = None
    ) -> WorkflowSubmissionResponse:
        return self.repository.submit(
            request.to_analysis_request(), max_attempts=2, user_id=user_id
        )

    def get(
        self,
        workflow_id: str,
        *,
        user_id: str | None = None,
        machine_only: bool = False,
    ) -> WorkflowStatusResponse:
        return self.repository.get(
            workflow_id, user_id=user_id, machine_only=machine_only
        )

    def cancel(
        self,
        workflow_id: str,
        *,
        user_id: str | None = None,
        machine_only: bool = False,
    ) -> WorkflowActionResponse:
        return self.repository.cancel(
            workflow_id, user_id=user_id, machine_only=machine_only
        )

    def retry(
        self,
        workflow_id: str,
        *,
        user_id: str | None = None,
        machine_only: bool = False,
    ) -> WorkflowActionResponse:
        return self.repository.retry(
            workflow_id, user_id=user_id, machine_only=machine_only
        )


_lock = threading.RLock()
_service: WorkflowService | None = None


def get_workflow_service() -> WorkflowService:
    global _service
    with _lock:
        if _service is None:
            _service = WorkflowService()
        return _service


def set_workflow_service(service: WorkflowService | None) -> None:
    global _service
    with _lock:
        _service = service


__all__ = ["WorkflowService", "get_workflow_service", "set_workflow_service"]
