"""One-shot evidence-backed question answering endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.auth import require_user
from backend.history_repository import (
    HistoryNotFoundError,
    HistoryRepository,
    get_history_repository,
)
from backend.services.ask_service import AskService
from backend.services.rag_service import get_rag_service
from mini_rag.api import MiniRAGService
from schemas.ask import AskRequest, AskResponse
from schemas.api import Page
from schemas.auth import AuthUser
from schemas.history import AskHistoryDetail, AskHistorySummary


router = APIRouter(prefix="/api/ask", tags=["Intelligence Ask"])


@router.post("", response_model=AskResponse)
def ask(
    request: AskRequest,
    http_request: Request,
    rag_service: MiniRAGService = Depends(get_rag_service),
    history: HistoryRepository = Depends(get_history_repository),
) -> AskResponse:
    try:
        response = AskService(rag_service).ask(request)
        user = getattr(http_request.state, "user", None)
        if user is not None:
            history.save_ask(user.user_id, request, response)
        return response
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Ask service unavailable: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/history", response_model=Page[AskHistorySummary])
def list_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: AuthUser = Depends(require_user),
    history: HistoryRepository = Depends(get_history_repository),
) -> Page[AskHistorySummary]:
    return history.list_asks(user.user_id, page=page, page_size=page_size)


@router.get("/history/{ask_id}", response_model=AskHistoryDetail)
def history_detail(
    ask_id: str,
    user: AuthUser = Depends(require_user),
    history: HistoryRepository = Depends(get_history_repository),
) -> AskHistoryDetail:
    try:
        return history.ask(user.user_id, ask_id)
    except HistoryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


__all__ = ["router"]
