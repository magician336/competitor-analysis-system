"""FastAPI application entry point for CodeRadar."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, Request, Response, status

from backend.routers.agents import router as agents_router
from backend.routers.benchmarks import router as benchmarks_router
from backend.routers.rag import IndexStatusResponse, router as rag_router
from backend.services.rag_service import get_rag_service
from mini_rag.api import MiniRAGService


def create_app() -> FastAPI:
    app = FastAPI(
        title="CodeRadar API",
        version="0.3.0",
        description="Traceable Mini-RAG plus evidence-backed LCEL Agent service.",
    )

    @app.middleware("http")
    async def declare_json_charset(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Make JSON response decoding deterministic for Windows PowerShell 5.1."""

        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if content_type.casefold() == "application/json":
            response.headers["content-type"] = "application/json; charset=utf-8"
        return response

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["system"], response_model=IndexStatusResponse)
    def readiness(
        response: Response,
        service: MiniRAGService = Depends(get_rag_service),
    ) -> dict[str, object]:
        result = service.health()
        if result.get("status") != "ready":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return result

    app.include_router(rag_router)
    app.include_router(agents_router)
    app.include_router(benchmarks_router)
    return app


app = create_app()
