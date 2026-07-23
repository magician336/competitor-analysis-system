"""One-shot evidence-backed question answering endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from backend.services.ask_service import AskService
from backend.services.rag_service import get_rag_service
from mini_rag.api import MiniRAGService
from schemas.ask import AskRequest, AskResponse


router = APIRouter(prefix="/api/ask", tags=["Intelligence Ask"])


@router.post("", response_model=AskResponse)
def ask(
    request: AskRequest,
    rag_service: MiniRAGService = Depends(get_rag_service),
) -> AskResponse:
    try:
        return AskService(rag_service).ask(request)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Ask service unavailable: {type(exc).__name__}: {exc}",
        ) from exc


__all__ = ["router"]
