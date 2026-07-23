"""HTTP API for durable asynchronous Multi-Agent workflows."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from backend.services.workflow_service import WorkflowService, get_workflow_service
from backend.workflow_repository import WorkflowConflictError, WorkflowNotFoundError
from schemas.workflow import (
    WorkflowActionResponse,
    WorkflowStatusResponse,
    WorkflowSubmissionResponse,
    WorkflowSubmitRequest,
)


router = APIRouter(prefix="/api/workflows", tags=["Workflows"])


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WorkflowNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workflow not found",
        )
    if isinstance(exc, WorkflowConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Workflow service unavailable: {type(exc).__name__}: {exc}",
    )


@router.post(
    "",
    response_model=WorkflowSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_workflow(
    request: WorkflowSubmitRequest,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowSubmissionResponse:
    try:
        return service.submit(request)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/{workflow_id}", response_model=WorkflowStatusResponse)
def get_workflow(
    workflow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowStatusResponse:
    try:
        return service.get(workflow_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/{workflow_id}/cancel",
    response_model=WorkflowActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_workflow(
    workflow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowActionResponse:
    try:
        return service.cancel(workflow_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/{workflow_id}/retry",
    response_model=WorkflowActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_workflow(
    workflow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowActionResponse:
    try:
        return service.retry(workflow_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


__all__ = ["router"]
