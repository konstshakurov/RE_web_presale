"""API модуля assign (KON-43): назначение кандидата, статус-действия, комментарий в Jira.

Права определяет Jira (BR-001): запись идёт токеном пользователя. Отказ Jira - текст как есть
(FR-029); истёкшая сессия - 409 ``needs_reauthorization`` (401 приложения не используется:
он означал бы конец сессии приложения). Пароль Jira вводится через ``POST /auth/jira-relogin``
(в запросах этого модуля пароля нет), введённые данные остаются на клиенте и отправляются
повторно; ``completed`` из ответа об ошибке передаётся обратно, чтобы не дублировать вложения.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from ..auth.deps import SessionContext
from ..integrations.jira import MAX_RATE_RUB
from ..jira_session.service import require_jira_ok
from .service import AssignError, AssignInput, AssignService

router = APIRouter(
    tags=["assign"],
    responses={401: {"description": "Требуется вход"}, 403: {"description": "CSRF или права Jira"}},
)

NEED_KEY = Path(pattern=r"^[A-Z][A-Z0-9_]*-[0-9]+$", description="Ключ потребности Jira")
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")

_HTTP = {
    "needs_reauthorization": (409, "needs_reauthorization"),
    "no_permission": (403, "no_permission"),
    "jira_unavailable": (503, "unavailable"),
    "not_enabled": (503, "not_enabled"),
    "auth_failed": (409, "needs_reauthorization"),
    "login_blocked": (409, "blocked"),
    "jira_error": (502, "blocked"),
    "need_not_found": (404, "not_found"),
    "employee_not_found": (404, "not_found"),
    "file_not_found": (404, "not_found"),
    "transition_unavailable": (409, "conflict"),
    "assign_unavailable": (409, "conflict"),
}


def _http_error(err: AssignError) -> HTTPException:
    status, state = _HTTP.get(err.code, (422, "invalid"))
    return HTTPException(status_code=status, detail={
        "state": state, "code": err.code, "message": err.message,
        "step": err.step, "completed": err.completed,
    })


class OptionOut(BaseModel):
    id: str
    name: str


class FieldOut(BaseModel):
    field_id: str
    name: str
    required: bool
    kind: str = Field(description="reason | rate | vat | currency | unit | other")
    options: list[OptionOut] = Field(description="Значения справочника Jira (не хранятся)")
    prefill_id: str | None = Field(None, description="Предзаполнение из потребности (ставка)")


class ActionOut(BaseModel):
    action: str = Field(description="search | hire | no_candidates")
    label: str
    target_status: str
    comment_required: bool
    fields: list[FieldOut]


class ActionsOut(BaseModel):
    need_key: str
    status: str
    actions: list[ActionOut] = Field(
        description="Белый список ∩ переходы Jira; пусто - кнопок под чипом нет")


class StatusIn(BaseModel):
    action: str = Field(description="search | hire | no_candidates")
    comment: str = Field("", max_length=32000, description="Для no_candidates обязателен")
    fields: dict[str, str] = Field(
        default_factory=dict, description="field_id -> id значения справочника Jira")


class AssignIn(BaseModel):
    employee_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]*-[0-9]+$")
    resume_file_id: int | None = Field(None, description="Пусто - Основное CV (FR-008)")
    extra_file_ids: list[int] = Field(default_factory=list, description="Доп. CV и Материалы")
    rate_rub: Decimal | None = Field(
        None, gt=0, le=MAX_RATE_RUB, description="«Ставка, руб. с НДС» (CR-008). Обязательна, если «Кто предлагает "
        "ставку» = «Производство» (БП-2); при «Клиент» не отправляется. Пишется в Jira отдельным "
        "вызовом после назначения")
    comment: str = Field("", max_length=32000)
    completed: list[str] = Field(
        default_factory=list, description="Шаги, выполненные прошлой попыткой (из ошибки)")


class ResultOut(BaseModel):
    need_key: str
    status: str | None = Field(description="Статус из Jira после операции; null - не перечитан")
    refreshed: bool
    steps: list[str]
    warnings: list[str] = Field(
        default_factory=list, description="Операция выполнена, но требует внимания оператора")
    rate_pending: bool = Field(
        False, description="Назначение выполнено, ставка не записана: повтор - тем же запросом "
        "с completed=steps (назначение не откатывается, FR-029)")


def _service(request: Request) -> AssignService:
    svc = getattr(request.app.state, "assign", None)
    if svc is None:
        raise HTTPException(status_code=503, detail={
            "state": "not_enabled", "code": "not_enabled",
            "message": "Запись в Jira не включена", "step": None, "completed": []})
    return svc


def _result(r) -> ResultOut:
    return ResultOut(need_key=r.need_key, status=r.status, refreshed=r.refreshed,
                     steps=list(r.steps), warnings=list(r.warnings),
                     rate_pending=r.rate_pending)


@router.get("/needs/{key}/status-actions", response_model=ActionsOut)
def status_actions(
    request: Request, key: Annotated[str, NEED_KEY],
    ctx: SessionContext = Depends(require_jira_ok),
) -> ActionsOut:
    """Кнопки статуса в столбце «Статус» (FR-011, FR-026)."""
    try:
        r = _service(request).list_actions(ctx.login, key)
    except AssignError as err:
        raise _http_error(err) from None
    return ActionsOut(
        need_key=r.need_key, status=r.status,
        actions=[ActionOut(
            action=a.action, label=a.label, target_status=a.target_status,
            comment_required=a.comment_required,
            fields=[FieldOut(
                field_id=f.field_id, name=f.name, required=f.required, kind=f.kind,
                options=[OptionOut(id=i, name=n) for i, n in f.options], prefill_id=f.prefill_id,
            ) for f in a.fields],
        ) for a in r.actions],
    )


@router.post("/needs/{key}/status", response_model=ResultOut)
def change_status(
    body: StatusIn, request: Request, key: Annotated[str, NEED_KEY],
    ctx: SessionContext = Depends(require_jira_ok),
) -> ResultOut:
    try:
        return _result(_service(request).change_status(
            ctx.login, key, body.action, comment=body.comment, fields=body.fields))
    except AssignError as err:
        raise _http_error(err) from None


@router.post("/needs/{key}/assign", response_model=ResultOut)
def assign(
    body: AssignIn, request: Request, key: Annotated[str, NEED_KEY],
    ctx: SessionContext = Depends(require_jira_ok),
) -> ResultOut:
    """Назначить кандидата: профиль, исполнитель, вложения, комментарий, переход «Кандидат
    назначен» (FR-008, FR-009, FR-010, FR-013), затем ставка отдельным вызовом ScriptRunner
    (CR-008, FR-018): только при «Производство». Сбой ставки - 200 с ``rate_pending`` и
    предупреждением, назначение не откатывается; истёкшая сессия Jira - 409
    ``needs_reauthorization`` с ``completed`` (повтор запишет только ставку)."""
    inp = AssignInput(
        employee_key=body.employee_key, resume_file_id=body.resume_file_id,
        extra_file_ids=tuple(body.extra_file_ids), rate_rub=body.rate_rub,
        comment=body.comment, completed=tuple(body.completed),
    )
    try:
        return _result(_service(request).assign(ctx.login, key, inp))
    except AssignError as err:
        raise _http_error(err) from None
