"""Browser authentication routes for CodeRadar."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from backend.auth import (
    SESSION_COOKIE_NAME,
    AuthRepository,
    UsernameConflictError,
    get_auth_repository,
    require_user,
)
from backend.config import load_api_settings
from schemas.auth import AuthUser, LoginRequest, RegisterRequest


router = APIRouter(prefix="/api/auth", tags=["Authentication"])


def _issue_session(
    response: Response,
    repository: AuthRepository,
    user: AuthUser,
) -> None:
    settings = load_api_settings()
    token = repository.create_session(
        user.user_id, ttl_seconds=settings.session_ttl_seconds
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", response_model=AuthUser, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    response: Response,
    repository: AuthRepository = Depends(get_auth_repository),
) -> AuthUser:
    try:
        user = repository.register(payload)
    except UsernameConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already registered.",
        ) from exc
    _issue_session(response, repository, user)
    return user


@router.post("/login", response_model=AuthUser)
def login(
    payload: LoginRequest,
    response: Response,
    repository: AuthRepository = Depends(get_auth_repository),
) -> AuthUser:
    user = repository.verify_credentials(payload.username, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    _issue_session(response, repository, user)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    _user: AuthUser = Depends(require_user),
    repository: AuthRepository = Depends(get_auth_repository),
) -> Response:
    repository.revoke_session(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=load_api_settings().cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=AuthUser)
def me(user: AuthUser = Depends(require_user)) -> AuthUser:
    return user


__all__ = ["router"]
