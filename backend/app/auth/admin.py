"""Роуты /admin/users: список допущенных (FR-021, UC-006, FR-031). Только role=admin."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..app_repo import AppRepo, AppUserDetail
from ..app_repo.repo import LAST_ADMIN, NOT_FOUND
from .deps import SessionContext, require_admin
from .ldap_port import normalize_login

log = logging.getLogger(__name__)

# require_admin: сессия + CSRF на изменяющих + role=admin (иначе 403), проверка на сервере
router = APIRouter(
    prefix="/admin/users",
    tags=["admin"],
    responses={401: {"description": "Требуется вход"}, 403: {"description": "Не админ или CSRF"}},
)

LAST_ADMIN_MSG = "Нельзя отозвать или понизить последнего активного администратора"


class UserOut(BaseModel):
    login: str
    role: Literal["user", "admin"]
    active: bool
    created_at: datetime
    updated_at: datetime


class AddUserRequest(BaseModel):
    login: str = Field(description="Логин LDAP; приводится к нижнему регистру, 1..128 символов")


class PatchUserRequest(BaseModel):
    active: bool | None = None
    role: Literal["user", "admin"] | None = None


def _out(u: AppUserDetail) -> UserOut:
    return UserOut(
        login=u.ldap_login, role=u.role, active=u.active,
        created_at=u.created_at, updated_at=u.updated_at,
    )


def _login(raw: str) -> str:
    login = normalize_login(raw)
    if not 1 <= len(login) <= 128:
        raise HTTPException(status_code=422, detail="Логин: от 1 до 128 символов")
    return login


def _repo(request: Request) -> AppRepo:
    return request.app.state.repo


@router.get("", response_model=list[UserOut])
def list_users(request: Request, _: SessionContext = Depends(require_admin)) -> list[UserOut]:
    return [_out(u) for u in _repo(request).list_users_detail()]


@router.post("", response_model=UserOut, responses={201: {"description": "Добавлен или возвращён"}})
def add_user(
    body: AddUserRequest,
    request: Request,
    response: Response,
    ctx: SessionContext = Depends(require_admin),
) -> UserOut:
    """Добавить логин; отозванный снова active=true. Повтор для активного: 200, без изменений."""
    user, changed = _repo(request).add_user(_login(body.login))
    response.status_code = 201 if changed else 200
    if changed:
        log.info("список допущенных: %s добавил %s", ctx.login, user.ldap_login)
    return _out(user)


@router.patch(
    "/{login}",
    response_model=UserOut,
    responses={404: {"description": "Нет в списке"}, 409: {"description": LAST_ADMIN_MSG}},
)
def patch_user(
    login: str,
    body: PatchUserRequest,
    request: Request,
    ctx: SessionContext = Depends(require_admin),
) -> UserOut:
    """active=false отзывает доступ и немедленно все сессии пользователя (UC-006 1а)."""
    login = _login(login)
    if body.active is None and body.role is None:
        raise HTTPException(status_code=422, detail="Укажите active и/или role")
    repo = _repo(request)
    result = repo.update_user(login, active=body.active, role=body.role)
    if result == NOT_FOUND:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if result == LAST_ADMIN:
        raise HTTPException(status_code=409, detail=LAST_ADMIN_MSG)
    log.info("список допущенных: %s изменил %s (active=%s, role=%s)",
             ctx.login, login, body.active, body.role)
    return _out(repo.get_user_detail(login))
