"""Backend configuration shared by the API and persistence layer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from mini_rag.config import MiniRAGSettings, load_settings
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = "sqlite:///data/runtime/coderadar.db"


def database_url() -> str:
    """Return a database URL independent of the process working directory."""

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    configured = os.getenv("CODERADAR_DATABASE_URL", "").strip()
    url = configured or DEFAULT_DATABASE_URL
    parsed = make_url(url)
    database = parsed.database
    if (
        not parsed.drivername.startswith("sqlite")
        or not database
        or database == ":memory:"
        or database.startswith("file:")
    ):
        return url
    path = Path(os.path.expandvars(database)).expanduser()
    if path.is_absolute():
        return url
    resolved = (PROJECT_ROOT / path).resolve()
    return parsed.set(database=resolved.as_posix()).render_as_string(
        hide_password=False
    )


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ApiSettings:
    auth_enabled: bool
    api_key: str
    read_rate_per_minute: int
    write_rate_per_minute: int
    cors_origins: tuple[str, ...]
    cookie_secure: bool
    session_ttl_seconds: int
    auth_rate_per_minute: int


def load_api_settings() -> ApiSettings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    origins = tuple(
        item.strip()
        for item in os.getenv(
            "CODERADAR_CORS_ORIGINS", "http://localhost:5173"
        ).split(",")
        if item.strip()
    )
    return ApiSettings(
        auth_enabled=_bool_env("CODERADAR_AUTH_ENABLED", True),
        api_key=os.getenv("CODERADAR_API_KEY", "").strip(),
        read_rate_per_minute=max(
            1, int(os.getenv("CODERADAR_READ_RATE_PER_MINUTE", "120"))
        ),
        write_rate_per_minute=max(
            1, int(os.getenv("CODERADAR_WRITE_RATE_PER_MINUTE", "30"))
        ),
        cors_origins=origins,
        cookie_secure=_bool_env("CODERADAR_COOKIE_SECURE", False),
        session_ttl_seconds=max(
            300, int(os.getenv("CODERADAR_SESSION_TTL_SECONDS", "604800"))
        ),
        auth_rate_per_minute=max(
            1, int(os.getenv("CODERADAR_AUTH_RATE_PER_MINUTE", "10"))
        ),
    )


__all__ = [
    "DEFAULT_DATABASE_URL",
    "MiniRAGSettings",
    "PROJECT_ROOT",
    "ApiSettings",
    "database_url",
    "load_api_settings",
    "load_settings",
]
