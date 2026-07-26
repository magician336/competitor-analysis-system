"""FastAPI application entry point for CodeRadar."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from backend.api_repository import (
    ApiConflictError,
    ApiObjectNotFound,
    get_formal_api_repository,
)
from backend.auth import SESSION_COOKIE_NAME, get_auth_repository
from backend.api_security import (
    SlidingWindowRateLimiter,
    audit_target,
    authenticate,
    client_ip_hash,
    problem_response,
    rate_identity,
    request_id_for,
)
from backend.config import load_api_settings
from backend.routers.admin import router as admin_router
from backend.routers.auth import router as auth_router
from backend.routers.agents import router as agents_router
from backend.routers.ask import router as ask_router
from backend.routers.benchmarks import router as benchmarks_router
from backend.routers.formal_api import router as formal_api_router
from backend.routers.rag import IndexStatusResponse, router as rag_router
from backend.routers.workflows import router as workflows_router
from backend.services.rag_service import get_rag_service
from backend.database import get_database
from mini_rag.api import MiniRAGService


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        database = get_database()
        database.check_connection()
        database.check_migration()
        yield

    app = FastAPI(
        title="CodeRadar API",
        version="0.5.0",
        description="Traceable intelligence, durable workflows, and formal query API.",
        lifespan=lifespan,
    )

    initial_settings = load_api_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(initial_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
        ],
    )
    rate_limiter = SlidingWindowRateLimiter()
    app.state.rate_limiter = rate_limiter

    @app.middleware("http")
    async def api_security_and_audit(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = request_id_for(request)
        request.state.user = None
        request.state.actor_type = "anonymous"
        started = time.perf_counter()
        settings = load_api_settings()
        rate_limit: int | None = None
        remaining: int | None = None
        if request.method.upper() == "OPTIONS":
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            public_auth_path = request.url.path in {
                "/api/auth/login",
                "/api/auth/register",
            }
            raw_session = request.cookies.get(SESSION_COOKIE_NAME)
            if raw_session:
                try:
                    provider = request.app.dependency_overrides.get(
                        get_auth_repository,
                        get_auth_repository,
                    )
                    request.state.user = provider().user_for_token(raw_session)
                except Exception:
                    request.state.user = None
            api_key_valid = authenticate(request, settings)
            if request.state.user is not None:
                request.state.actor_type = "user"
            elif api_key_valid:
                request.state.actor_type = "api_key"
            elif not settings.auth_enabled:
                request.state.actor_type = "anonymous"
            elif not public_auth_path:
                return problem_response(
                    request=request,
                    status_code=401,
                    code="authentication_required",
                    title="Authentication required",
                    detail="Please sign in to continue.",
                    headers={"WWW-Authenticate": "Session, ApiKey"},
                )
            if public_auth_path:
                rate_limit = settings.auth_rate_per_minute
                ip = request.client.host if request.client else "unknown"
                rate_key = f"auth:{ip}"
            elif settings.auth_enabled:
                rate_limit = (
                    settings.read_rate_per_minute
                    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}
                    else settings.write_rate_per_minute
                )
                rate_key = (
                    f"{request.method.upper()}:"
                    f"{rate_identity(request, user_id=getattr(request.state.user, 'user_id', None))}"
                )
            else:
                rate_key = ""
            if rate_limit is not None:
                allowed, remaining, retry_after = rate_limiter.consume(
                    rate_key,
                    limit=rate_limit,
                )
                if not allowed:
                    return problem_response(
                        request=request,
                        status_code=429,
                        code="rate_limit_exceeded",
                        title="Too many requests",
                        detail="The API rate limit has been exceeded.",
                        headers={
                            "Retry-After": str(retry_after),
                            "X-RateLimit-Limit": str(rate_limit),
                            "X-RateLimit-Remaining": "0",
                            "X-RateLimit-Reset": str(retry_after),
                        },
                    )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if rate_limit is not None and remaining is not None:
            response.headers["X-RateLimit-Limit"] = str(rate_limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = "60"
        target = audit_target(request)
        if target is not None:
            action, resource_type, resource_id = target
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            try:
                provider = request.app.dependency_overrides.get(
                    get_formal_api_repository,
                    get_formal_api_repository,
                )
                provider().record_audit(
                    event_id="audit_" + uuid.uuid4().hex[:20],
                    request_id=request.state.request_id,
                    action=action,
                    method=request.method.upper(),
                    path=request.url.path,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    status_code=response.status_code,
                    duration_ms=elapsed_ms,
                    client_ip_hash=client_ip_hash(request),
                )
            except Exception:
                # Audit failure must not leak data or corrupt a completed API response.
                pass
        return response

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

    @app.exception_handler(ApiObjectNotFound)
    async def api_not_found(request: Request, exc: ApiObjectNotFound) -> JSONResponse:
        return problem_response(
            request=request,
            status_code=404,
            code="resource_not_found",
            title="Resource not found",
            detail=str(exc),
        )

    @app.exception_handler(ApiConflictError)
    async def api_conflict(request: Request, exc: ApiConflictError) -> JSONResponse:
        return problem_response(
            request=request,
            status_code=409,
            code="resource_conflict",
            title="Resource conflict",
            detail=str(exc),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "location": [str(item) for item in error.get("loc", ())],
                "message": error.get("msg", "invalid value"),
                "type": error.get("type", "validation_error"),
            }
            for error in exc.errors()
        ]
        return problem_response(
            request=request,
            status_code=422,
            code="validation_error",
            title="Request validation failed",
            detail="One or more request values are invalid.",
            errors=errors,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        safe_detail = (
            "The dependent service is temporarily unavailable."
            if exc.status_code >= 500
            else str(exc.detail)
        )
        return problem_response(
            request=request,
            status_code=exc.status_code,
            code={
                400: "bad_request",
                401: "unauthorized",
                403: "forbidden",
                404: "resource_not_found",
                409: "resource_conflict",
                429: "rate_limit_exceeded",
                503: "service_unavailable",
            }.get(exc.status_code, "http_error"),
            title="Request failed",
            detail=safe_detail,
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, _exc: Exception) -> JSONResponse:
        return problem_response(
            request=request,
            status_code=500,
            code="internal_error",
            title="Internal server error",
            detail="The server could not complete the request.",
        )

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

    app.include_router(auth_router)
    app.include_router(rag_router)
    app.include_router(admin_router)
    app.include_router(ask_router)
    app.include_router(agents_router)
    app.include_router(benchmarks_router)
    app.include_router(formal_api_router)
    app.include_router(workflows_router)

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        components.setdefault("securitySchemes", {})["ApiKeyAuth"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
        }
        components.setdefault("securitySchemes", {})["SessionAuth"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": SESSION_COOKIE_NAME,
        }
        for path, item in schema.get("paths", {}).items():
            if path.startswith("/api/"):
                for operation in item.values():
                    if isinstance(operation, dict):
                        if path in {"/api/auth/login", "/api/auth/register"}:
                            operation["security"] = []
                        else:
                            operation["security"] = [
                                {"SessionAuth": []},
                                {"ApiKeyAuth": []},
                            ]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
    return app


app = create_app()
