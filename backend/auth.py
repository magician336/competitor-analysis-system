"""Username/password authentication and opaque server-side sessions."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.database import get_database
from backend.models import UserRecord, UserSessionRecord
from schemas.auth import AuthUser, RegisterRequest, normalize_username


SESSION_COOKIE_NAME = "coderadar_session"


class UsernameConflictError(RuntimeError):
    pass


class AuthRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        self.session_factory = session_factory or get_database().session_factory
        self.password_hasher = password_hasher or PasswordHasher()
        self._dummy_hash = self.password_hasher.hash("CodeRadar-dummy-password-2026")

    def register(self, request: RegisterRequest) -> AuthUser:
        user = UserRecord(
            user_id="usr_" + uuid4().hex[:20],
            username=request.username,
            username_normalized=normalize_username(request.username),
            password_hash=self.password_hasher.hash(request.password),
        )
        try:
            with self.session_factory.begin() as session:
                session.add(user)
        except IntegrityError as exc:
            raise UsernameConflictError("username is already registered") from exc
        return self._public(user)

    def verify_credentials(self, username: str, password: str) -> AuthUser | None:
        normalized = normalize_username(username)
        with self.session_factory.begin() as session:
            record = session.scalar(
                select(UserRecord).where(
                    UserRecord.username_normalized == normalized
                )
            )
            encoded = record.password_hash if record is not None else self._dummy_hash
            try:
                valid = self.password_hasher.verify(encoded, password)
            except (InvalidHashError, VerifyMismatchError):
                valid = False
            if record is None or not valid:
                return None
            if self.password_hasher.check_needs_rehash(record.password_hash):
                record.password_hash = self.password_hasher.hash(password)
            return self._public(record)

    def create_session(self, user_id: str, *, ttl_seconds: int) -> str:
        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            session.execute(
                delete(UserSessionRecord).where(
                    UserSessionRecord.expires_at <= now
                )
            )
            session.add(
                UserSessionRecord(
                    session_id="ses_" + uuid4().hex[:20],
                    user_id=user_id,
                    token_hash=self._token_hash(raw_token),
                    created_at=now,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                    last_seen_at=now,
                )
            )
        return raw_token

    def user_for_token(self, raw_token: str | None) -> AuthUser | None:
        if not raw_token:
            return None
        now = datetime.now(timezone.utc)
        token_hash = self._token_hash(raw_token)
        with self.session_factory.begin() as session:
            record = session.scalar(
                select(UserSessionRecord).where(
                    UserSessionRecord.token_hash == token_hash
                )
            )
            if record is None:
                return None
            expires_at = record.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                session.delete(record)
                return None
            user = session.get(UserRecord, record.user_id)
            if user is None:
                session.delete(record)
                return None
            last_seen_at = record.last_seen_at
            if last_seen_at.tzinfo is None:
                last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
            if now - last_seen_at >= timedelta(minutes=5):
                record.last_seen_at = now
            return self._public(user)

    def revoke_session(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        with self.session_factory.begin() as session:
            session.execute(
                delete(UserSessionRecord).where(
                    UserSessionRecord.token_hash == self._token_hash(raw_token)
                )
            )

    def create_user_if_missing(
        self,
        *,
        username: str,
        password: str,
    ) -> tuple[AuthUser, bool]:
        normalized = normalize_username(username)
        with self.session_factory() as session:
            existing = session.scalar(
                select(UserRecord).where(
                    UserRecord.username_normalized == normalized
                )
            )
            if existing is not None:
                return self._public(existing), False
        user = self.register(RegisterRequest(username=username, password=password))
        return user, True

    @staticmethod
    def _token_hash(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _public(record: UserRecord) -> AuthUser:
        return AuthUser(
            user_id=record.user_id,
            username=record.username,
            created_at=record.created_at,
        )


_repository: AuthRepository | None = None


def get_auth_repository() -> AuthRepository:
    global _repository
    if _repository is None:
        _repository = AuthRepository()
    return _repository


def optional_user(request: Request) -> AuthUser | None:
    return getattr(request.state, "user", None)


def actor_ownership(request: Request) -> tuple[str | None, bool]:
    """Return (user_id, machine_only) for ownership-aware repositories."""

    user = optional_user(request)
    if user is not None:
        return user.user_id, False
    return None, getattr(request.state, "actor_type", "anonymous") == "api_key"


def require_user(request: Request) -> AuthUser:
    user = optional_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please sign in to continue.",
            headers={"WWW-Authenticate": "Session"},
        )
    return user


__all__ = [
    "AuthRepository",
    "SESSION_COOKIE_NAME",
    "UsernameConflictError",
    "actor_ownership",
    "get_auth_repository",
    "optional_user",
    "require_user",
]
