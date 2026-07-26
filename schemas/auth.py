"""Public contracts for CodeRadar username/password authentication."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


def normalize_username(value: str) -> str:
    return value.strip().casefold()


class CredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not USERNAME_PATTERN.fullmatch(value):
            raise ValueError(
                "username must contain only letters, numbers, underscores, or hyphens"
            )
        return value


class RegisterRequest(CredentialsRequest):
    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not any(character.isalpha() for character in value) or not any(
            character.isdigit() for character in value
        ):
            raise ValueError("password must contain at least one letter and one number")
        return value


class LoginRequest(BaseModel):
    """Accept candidate credentials broadly so failures stay non-enumerating."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=72)


class AuthUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    username: str
    created_at: datetime


__all__ = [
    "AuthUser",
    "CredentialsRequest",
    "LoginRequest",
    "RegisterRequest",
    "normalize_username",
]
