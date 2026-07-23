"""Backend configuration shared by the API and persistence layer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mini_rag.config import MiniRAGSettings, load_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = "sqlite:///data/runtime/coderadar.db"


def database_url() -> str:
    """Return the configured database URL with a stable project-relative default."""

    configured = os.getenv("CODERADAR_DATABASE_URL", "").strip()
    if configured:
        return configured
    return DEFAULT_DATABASE_URL


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


def load_api_settings() -> ApiSettings:
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
