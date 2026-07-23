"""API authentication, rate limiting, request IDs, and safe audit helpers."""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.config import ApiSettings
from schemas.api import ProblemDetails


_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def request_id_for(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "").strip()
    if supplied and _REQUEST_ID_PATTERN.fullmatch(supplied):
        return supplied
    return uuid.uuid4().hex


def problem_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    errors: list[dict] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", request_id_for(request))
    payload = ProblemDetails(
        type=f"https://coderadar.local/problems/{code}",
        title=title,
        status=status_code,
        detail=detail,
        code=code,
        request_id=request_id,
        instance=request.url.path,
        errors=errors or [],
    )
    response = JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        media_type="application/problem+json",
        headers=headers,
    )
    response.headers["X-Request-ID"] = request_id
    return response


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def consume(self, key: str, *, limit: int, now: float | None = None) -> tuple[bool, int, int]:
        current = time.monotonic() if now is None else now
        cutoff = current - 60.0
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(60.0 - (current - events[0])) + 1)
                return False, 0, retry_after
            events.append(current)
            return True, limit - len(events), 0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


def authenticate(request: Request, settings: ApiSettings) -> bool:
    if not settings.auth_enabled:
        return True
    supplied = request.headers.get("X-API-Key", "")
    return bool(settings.api_key) and hmac.compare_digest(supplied, settings.api_key)


def rate_identity(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    key = request.headers.get("X-API-Key", "disabled")
    digest = hashlib.sha256(f"{key}\0{ip}".encode("utf-8")).hexdigest()
    return digest


def client_ip_hash(request: Request) -> str | None:
    if request.client is None:
        return None
    return hashlib.sha256(request.client.host.encode("utf-8")).hexdigest()


def audit_target(request: Request) -> tuple[str, str | None, str | None] | None:
    path = request.url.path
    method = request.method.upper()
    parts = [part for part in path.split("/") if part]
    if method != "GET" and path.startswith("/api/"):
        resource_type = parts[1] if len(parts) > 1 else None
        resource_id = parts[2] if len(parts) > 2 else None
        return f"{method.lower()}_{resource_type or 'api'}", resource_type, resource_id
    if method == "GET" and path.startswith("/api/evidence/"):
        return "view_evidence", "evidence", parts[-1]
    if method == "GET" and path.startswith("/api/briefings/"):
        return "view_briefing", "briefing", parts[2] if len(parts) > 2 else None
    return None


__all__ = [
    "SlidingWindowRateLimiter",
    "audit_target",
    "authenticate",
    "client_ip_hash",
    "problem_response",
    "rate_identity",
    "request_id_for",
]
