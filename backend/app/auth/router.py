"""Роуты /auth: login, logout, me (FR-024, FR-021, ADR-008)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..app_repo import AppRepo
from ..config import Settings
from .deps import SESSION_COOKIE, SessionContext, authenticated, authenticated_csrf
from .hooks import run_after_login
from .ldap_port import LdapUnavailable, normalize_login
from .sessions import csrf_token, expires_at, hash_session_id, new_session_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

DENIED = "Неверный логин или пароль, либо нет доступа"
LDAP_DOWN = "Сервис входа временно недоступен, повторите позже"

#: Разделы верхнего меню (FR-031); «Настройки» только для администратора.
SECTIONS = (
    {"key": "needs", "title": "Потребности", "admin_only": False},
    {"key": "employees", "title": "Сотрудники", "admin_only": False},
    {"key": "resume", "title": "Резюме", "admin_only": False},
    {"key": "settings", "title": "Настройки", "admin_only": True},
)


class LoginRequest(BaseModel):
    login: str = Field(max_length=128)
    password: str = Field(max_length=1024, repr=False)  # пустые отклоняются в обработчике


class Section(BaseModel):
    key: str
    title: str
    admin_only: bool


class JiraStatus(BaseModel):
    state: str = Field(
        description="ok | blocked (отказ Jira: пароль, права, подтверждение входа) | "
        "unavailable (Jira недоступна или вход не включён) | not_checked"
    )
    code: str = Field("", description="auth_failed | login_blocked | no_permission | "
                      "jira_unavailable | not_enabled | jira_error; пусто при ok")
    message: str = Field("", description="Текст отказа Jira как есть (FR-029)")


class JiraReloginRequest(BaseModel):
    password: str = Field(max_length=1024, repr=False)


class MeResponse(BaseModel):
    login: str
    role: str
    jira_status: JiraStatus
    sections: list[Section]
    csrf_token: str = Field(description="Заголовок X-CSRF-Token на изменяющих запросах")


def _jira_status(state, login: str) -> JiraStatus:
    service = getattr(state, "jira_session", None)
    if service is None:
        return JiraStatus(state="not_checked")
    info = service.status(login)
    return JiraStatus(state=info.state, code=info.code, message=info.message)


def _me(ctx: SessionContext, settings: Settings, jira: JiraStatus) -> MeResponse:
    is_admin = ctx.role == "admin"
    return MeResponse(
        login=ctx.login,
        role=ctx.role,
        jira_status=jira,
        sections=[Section(**s) for s in SECTIONS if is_admin or not s["admin_only"]],
        csrf_token=csrf_token(settings.enc_key_bytes(), ctx.session_id),
    )


@router.post(
    "/login",
    response_model=MeResponse,
    responses={401: {"description": DENIED}, 503: {"description": LDAP_DOWN}},
)
def login(body: LoginRequest, request: Request, response: Response) -> MeResponse:
    """LDAP bind, затем проверка списка допущенных, затем сессия (cookie HttpOnly)."""
    state = request.app.state
    settings: Settings = state.settings
    repo: AppRepo = state.repo
    ldap = getattr(state, "ldap", None)
    if ldap is None:
        log.error("вход невозможен: LDAP не настроен")
        raise HTTPException(status_code=503, detail=LDAP_DOWN)

    login_name = normalize_login(body.login)
    pw = body.password
    if not login_name or not pw:
        raise HTTPException(status_code=401, detail=DENIED)
    try:
        ok = ldap.authenticate(login_name, pw)
    except LdapUnavailable:
        raise HTTPException(status_code=503, detail=LDAP_DOWN) from None
    if not ok:
        log.info("вход отклонён: неверные данные")
        raise HTTPException(status_code=401, detail=DENIED)

    user = repo.get_user(login_name)
    if user is None or not user.active:
        log.info("вход отклонён: нет в списке допущенных")
        raise HTTPException(status_code=401, detail=DENIED)  # тот же текст, сессии нет

    session_id = new_session_id()
    repo.create_session(
        hash_session_id(session_id), user.ldap_login, expires_at(settings.session_ttl_hours)
    )
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=int(settings.session_ttl_hours * 3600),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    run_after_login(getattr(state, "after_login_hooks", ()), user.ldap_login, pw)
    log.info("вход выполнен: %s", user.ldap_login)
    return _me(
        SessionContext(user.ldap_login, user.role, session_id),
        settings,
        _jira_status(state, user.ldap_login),
    )


@router.post("/logout", status_code=204, responses={401: {}, 403: {"description": "CSRF"}})
def logout(
    request: Request, response: Response, ctx: SessionContext = Depends(authenticated_csrf)
) -> None:
    request.app.state.repo.revoke_session(hash_session_id(ctx.session_id))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeResponse, responses={401: {}})
def me(request: Request, ctx: SessionContext = Depends(authenticated)) -> MeResponse:
    state = request.app.state
    return _me(ctx, state.settings, _jira_status(state, ctx.login))


@router.post(
    "/jira-relogin",
    response_model=MeResponse,
    responses={401: {}, 403: {"description": "CSRF"}, 503: {"description": "Jira не настроена"}},
)
def jira_relogin(
    body: JiraReloginRequest,
    request: Request,
    ctx: SessionContext = Depends(authenticated_csrf),
) -> MeResponse:
    """Повторный ввод пароля при истёкшей сессии Jira. Как вход LDAP не проверяется;
    пароль только в памяти запроса, отказ Jira становится jira_status, а не ошибкой HTTP."""
    state = request.app.state
    service = getattr(state, "jira_session", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Сессия Jira не настроена")
    if not body.password:
        raise HTTPException(status_code=422, detail="Пароль не указан")
    service.establish(ctx.login, body.password)
    return _me(ctx, state.settings, _jira_status(state, ctx.login))
