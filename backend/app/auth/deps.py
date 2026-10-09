"""Зависимости FastAPI: сессия на каждый запрос, CSRF на изменяющие, проверка admin."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyCookie, APIKeyHeader

from ..app_repo import AppRepo
from ..config import Settings
from .sessions import csrf_valid, hash_session_id

SESSION_COOKIE = "re_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Схемы попадают в OpenAPI для фронтенда
_cookie_scheme = APIKeyCookie(name=SESSION_COOKIE, auto_error=False, scheme_name="SessionCookie")
_csrf_scheme = APIKeyHeader(name=CSRF_HEADER, auto_error=False, scheme_name="CsrfToken")


@dataclass(frozen=True)
class SessionContext:
    login: str
    role: str
    session_id: str
    display_name: str | None = None


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Требуется вход")


def authenticated(
    request: Request, session_id: str | None = Security(_cookie_scheme)
) -> SessionContext:
    """Сессия жива (не истекла, не отозвана), пользователь в списке и active; last_seen_at."""
    if not session_id:
        raise _unauthorized()
    repo: AppRepo = request.app.state.repo
    h = hash_session_id(session_id)
    active = repo.get_active_session(h)
    if active is None:
        raise _unauthorized()
    user = repo.get_user(active[0])
    if user is None or not user.active:
        repo.revoke_session(h)  # отзыв доступа действует немедленно (FR-021, UC-006)
        raise _unauthorized()
    repo.touch_session(h)
    return SessionContext(
        login=user.ldap_login, role=user.role, session_id=session_id,
        display_name=user.display_name,
    )


def authenticated_csrf(
    request: Request,
    ctx: SessionContext = Depends(authenticated),
    token: str | None = Security(_csrf_scheme),
) -> SessionContext:
    """То же, плюс заголовок X-CSRF-Token для изменяющих методов."""
    if request.method not in SAFE_METHODS:
        settings: Settings = request.app.state.settings
        if not csrf_valid(settings.enc_key_bytes(), ctx.session_id, token):
            raise HTTPException(status_code=403, detail="Неверный или отсутствующий CSRF-токен")
    return ctx


def require_admin(ctx: SessionContext = Depends(authenticated_csrf)) -> SessionContext:
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return ctx
