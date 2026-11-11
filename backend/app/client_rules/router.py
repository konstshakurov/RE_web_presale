"""API тезисов правил клиента из комментариев эпика (KON-160): кэш по хешу + ручной пересчёт.

GET - из кэша и никогда не ждёт Jira/LLM: хеш устарел -> 202 «Обновляется…» (задача
пересчёта ставится в этот же запрос). POST regenerate - только админ (KON-160, п. 5).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth.deps import SessionContext, authenticated, require_admin
from .service import ClientRulesError, ClientRulesService

router = APIRouter(tags=["client-rules"])


class SourceOut(BaseModel):
    id: str = Field(description="id комментария Jira (для ссылки)")
    author: str | None = None
    date: str | None = None


class ThesisOut(BaseModel):
    text: str
    contradicts_rules: bool = Field(
        False, description="true - тезис противоречит полевому правилу клиента (KON-160, п. 4)")
    sources: list[SourceOut] = []


class ThesesOut(BaseModel):
    """Тезисы правил клиента; статус отражает свежесть кэша (hash комментариев)."""

    status: str = Field(description="ok | regenerating (202) | empty (комментариев нет)")
    need_key: str
    epic_key: str
    theses: list[ThesisOut]
    generated_at: str | None = None
    provider: str | None = None
    task_submitted: bool | None = Field(
        None, description="regenerating: поставлена ли задача пересчёта в этот запрос")


class RegenerateIn(BaseModel):
    provider_id: int | None = Field(
        None, description="Провайдер LLM; пусто - по умолчанию (внутренняя Qwen)")

class RegenerateOut(BaseModel):
    task_id: str
    status: str
    requires_confirmation: bool = Field(
        description="true - внешний провайдер: подтвердить отправку в «Задачах LLM» (FR-023)")
    provider: str


def _service(request: Request) -> ClientRulesService:
    return request.app.state.client_rules


def _http_error(err: ClientRulesError) -> HTTPException:
    status = err.status
    if status is None:
        status = 500 if err.code == "gateway_error" else 502
    return HTTPException(status_code=status, detail={"code": err.code, "message": err.message})


@router.get(
    "/needs/{need_key}/client-rules/theses", response_model=ThesesOut,
    responses={202: {"model": ThesesOut, "description": "Тезисы считаются - «Обновляется…»"}},
)
def get_theses(
    need_key: str, request: Request, ctx: SessionContext = Depends(authenticated),
) -> ThesesOut:
    try:
        data = _service(request).get(need_key, ctx.login)
    except ClientRulesError as err:
        raise _http_error(err) from None
    return ThesesOut(**data)


@router.post(
    "/needs/{need_key}/client-rules/regenerate", response_model=RegenerateOut,
    responses={409: {"description": "Потребность без клиента / у эпика нет комментариев"}},
)
def regenerate(
    need_key: str, request: Request, body: RegenerateIn | None = None,
    ctx: SessionContext = Depends(require_admin),
) -> RegenerateOut:
    """Ручной пересчёт тезисов (владелец, KON-160 п. 3). Задача от логина админа: внешний
    провайдер потребует подтверждения отправки в «Задачах LLM» (FR-023)."""
    service = _service(request)
    try:
        data = service.regenerate(need_key, ctx.login,
                                  provider_id=(body.provider_id if body else None))
    except ClientRulesError as err:
        raise _http_error(err) from None
    return RegenerateOut(**data)
