"""API кэша Jira: списки потребностей и сотрудников, группы статусов, эпик клиента, «Обновить»
(FR-001, FR-002, FR-020, FR-028, FR-034, FR-035). Списки читаются только из кэша и никогда не
ждут Jira; при отказе Jira кэш отдаётся с пометкой (``sync.stale``) и текстом отказа как есть.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth.deps import SessionContext, authenticated
from ..jira_session.service import require_jira_ok
from .repo import SCOPE_EMPLOYEES, SCOPE_NEEDS, NeedFilter
from .service import (
    STATE_NEEDS_REAUTH,
    STATE_NOT_ENABLED,
    STATE_UNAVAILABLE,
    SyncError,
    SyncInfo,
    SyncService,
)

router = APIRouter(prefix="/sync", tags=["sync"])

LIMIT_MAX = 500


class SyncStamp(BaseModel):
    """Штамп «актуально на» и состояние опроса (FR-028, FR-029)."""

    scope: Literal["needs", "employees"]
    state: str = Field(
        description="ok | never_synced | paused (опрос приостановлен: нет активных "
        "пользователей) | "
        "needs_reauthorization (сессия Jira истекла) | no_permission | jira_unavailable | "
        "jira_error | not_enabled | error"
    )
    stale: bool = Field(description="true - данные из кэша могут отставать: показать пометку")
    last_success_at: datetime | None = Field(None, description="«Актуально на»")
    last_attempt_at: datetime | None = None
    code: str = Field("", description="Код последнего отказа Jira; пусто, если опрос успешен")
    message: str = Field("", description="Текст отказа Jira как есть (FR-029)")
    paused: bool = False
    interval_sec: float = Field(description="Период опроса")


class Rate(BaseModel):
    amount: float | None = None
    currency: str | None = Field(None, description="RUB | USD | KZT")
    includes_vat: bool | None = None
    unit: str | None = None
    proposer: str | None = None


class NeedOut(BaseModel):
    jira_key: str
    jira_url: str | None = Field(None, description="Ссылка на задачу в Jira (нужен JIRA_BASE_URL)")
    title: str
    description: str | None = None
    requirements_text: str | None = None
    status: str = Field(description="Статус Jira как есть (не резолюция, BR-010)")
    epic_key: str | None = None
    epic_name: str | None = Field(None, description="Клиент; пока эпик не загружен - epic_key")
    employee_key: str | None = None
    assignee: str | None = None
    author: str | None = None
    contract_type: str | None = None
    rate: Rate
    tags: list[str] = []
    jira_created_at: datetime
    jira_updated_at: datetime
    synced_at: datetime


class NeedsPage(BaseModel):
    items: list[NeedOut]
    total: int = Field(description="Число потребностей по фильтру (без учёта limit/offset)")
    limit: int
    offset: int
    sync: SyncStamp


class StatusGroupOut(BaseModel):
    id: int | None = Field(None, description="null у виртуальных групп 'all' и 'other'")
    key: str = Field(description="Значение параметра group: число (id), 'other' или 'all'")
    name: str
    count: int


class StatusCount(BaseModel):
    status: str
    count: int


class NeedGroups(BaseModel):
    groups: list[StatusGroupOut]
    statuses: list[StatusCount]
    sync: SyncStamp


class EmployeeOut(BaseModel):
    jira_key: str
    jira_url: str | None = None
    full_name: str
    status: str
    assignable: bool
    extra_experience: str | None = Field(
        None, description="Подробнее об опыте (customfield_13504) - «описание» FR-002")
    vacation_plan: str | None = Field(None, description="Поле Jira «Отпуск» как есть (FR-002)")
    note: str | None = None
    note_author: str | None = None
    note_updated_at: datetime | None = None
    synced_at: datetime


class EmployeesPage(BaseModel):
    items: list[EmployeeOut]
    total: int
    limit: int
    offset: int
    statuses: list[StatusCount]
    sync: SyncStamp


class RefreshRequest(BaseModel):
    scope: Literal["needs", "employees"]


class RefreshResult(BaseModel):
    scope: Literal["needs", "employees"]
    ok: bool
    busy: bool = Field(False, description="true - опрос уже идёт, повторно не запускался")
    fetched: int = 0
    upserted: int = 0
    skipped: int = 0
    sync: SyncStamp


class SyncErrorBody(BaseModel):
    """Тело отказа «Обновить» / эпика: причина как есть (FR-029)."""

    state: str = Field(description="needs_reauthorization | no_permission | unavailable | blocked "
                       "| not_enabled | error")
    code: str
    message: str


class RuleOut(BaseModel):
    id: str
    name: str
    value: str


class EpicOut(BaseModel):
    epic_key: str
    name: str
    summary: str | None = None
    rules: list[RuleOut] = Field(description="Только заполненные поля правил клиента (FR-015)")
    fetched_at: datetime | None = Field(None, description="Когда правила получены из Jira")
    stale: bool = Field(description="true - Jira отказала, показан кэш")
    error: SyncErrorBody | None = None
    needs_total: int = Field(description="Потребностей клиента в кэше")


def _service(request: Request) -> SyncService:
    return request.app.state.sync


def _stamp(info: SyncInfo) -> SyncStamp:
    return SyncStamp(
        scope=info.scope, state=info.state, stale=info.stale,
        last_success_at=info.last_success_at, last_attempt_at=info.last_attempt_at,
        code=info.code, message=info.message, paused=info.paused,
        interval_sec=info.interval_sec,
    )


def _url(service: SyncService, key: str) -> str | None:
    base = service.settings.jira_base_url
    return f"{base}/browse/{key}" if base else None


def _http_error(err: SyncError) -> HTTPException:
    """401 приложения не используется: он означал бы конец сессии приложения. Истёкшая сессия
    Jira - 409 ``needs_reauthorization``; недоступность - 503; прочее - 502 с текстом Jira."""
    if err.code == "bad_request":
        return HTTPException(status_code=422, detail=err.message)
    if err.code == STATE_NEEDS_REAUTH:
        status, state = 409, "needs_reauthorization"
    elif err.code == "no_permission":
        status, state = 403, "no_permission"
    elif err.code == STATE_UNAVAILABLE:
        status, state = 503, "unavailable"
    elif err.code == STATE_NOT_ENABLED:
        status, state = 503, "not_enabled"
    else:
        status, state = 502, "blocked" if err.code == "jira_error" else "error"
    return HTTPException(
        status_code=status, detail={"state": state, "code": err.code, "message": err.message}
    )


@router.get("/needs", response_model=NeedsPage)
def list_needs(
    request: Request,
    _: SessionContext = Depends(authenticated),
    group: Annotated[str | None, Query(
        pattern=r"^(all|other|\d{1,9})$",
        description="Группа статусов: id группы, 'other' (Прочие) или 'all' (по умолчанию)",
    )] = None,
    status: Annotated[str | None, Query(max_length=128)] = None,
    epic_key: Annotated[str | None, Query(max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=200, description="Поиск по ключу и названию")] = None,
    sort: Literal["created", "updated"] = "created",
    limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NeedsPage:
    service = _service(request)
    items, total = service.repo.list_needs(
        NeedFilter(group=group, status=status, epic_key=epic_key, query=q),
        limit=limit, offset=offset, sort=sort,
    )
    out = [
        NeedOut(
            **{k: v for k, v in it.items() if not k.startswith("rate_")},
            jira_url=_url(service, it["jira_key"]),
            rate=Rate(
                amount=None if it["rate_amount"] is None else float(it["rate_amount"]),
                currency=it["rate_currency"],
                includes_vat=it["rate_includes_vat"], unit=it["rate_unit"],
                proposer=it["rate_proposer"],
            ),
        )
        for it in items
    ]
    return NeedsPage(
        items=out, total=total, limit=limit, offset=offset,
        sync=_stamp(service.sync_info(SCOPE_NEEDS)),
    )


@router.get("/needs/groups", response_model=NeedGroups)
def need_groups(request: Request, _: SessionContext = Depends(authenticated)) -> NeedGroups:
    """Группы статусов со счётчиками (FR-035): состав групп хранится в «Настройках»;
    'Прочие' и 'Все статусы' считаются здесь, поэтому потребность не пропадает из интерфейса."""
    service = _service(request)
    data = service.repo.need_group_counts()
    groups = [
        StatusGroupOut(id=g[0], key=str(g[0]), name=g[1], count=g[3]) for g in data["groups"]
    ]
    groups.append(StatusGroupOut(id=None, key="other", name="Прочие", count=data["other"]))
    groups.append(StatusGroupOut(id=None, key="all", name="Все статусы", count=data["total"]))
    return NeedGroups(
        groups=groups,
        statuses=[StatusCount(status=s, count=c) for s, c in data["statuses"]],
        sync=_stamp(service.sync_info(SCOPE_NEEDS)),
    )


@router.get("/employees", response_model=EmployeesPage)
def list_employees(
    request: Request,
    _: SessionContext = Depends(authenticated),
    status: Annotated[str | None, Query(max_length=128)] = None,
    assignable: bool | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Поиск по ФИО")] = None,
    limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EmployeesPage:
    service = _service(request)
    if not service.repo.has_cv:
        raise HTTPException(
            status_code=503,
            detail={"state": "not_enabled", "code": "not_enabled",
                    "message": "База сотрудников не настроена (CV_DB_URL)"},
        )
    items, total = service.repo.list_employees(
        status=status, assignable=assignable, query=q, limit=limit, offset=offset
    )
    counts = service.repo.employee_status_counts()
    return EmployeesPage(
        items=[EmployeeOut(**it, jira_url=_url(service, it["jira_key"])) for it in items],
        total=total, limit=limit, offset=offset,
        statuses=[StatusCount(status=s, count=c) for s, c in counts],
        sync=_stamp(service.sync_info(SCOPE_EMPLOYEES)),
    )


@router.get("/status", response_model=list[SyncStamp])
def sync_status(request: Request, _: SessionContext = Depends(authenticated)) -> list[SyncStamp]:
    """Штампы обеих областей (для шапки интерфейса)."""
    service = _service(request)
    return [_stamp(service.sync_info(SCOPE_NEEDS)), _stamp(service.sync_info(SCOPE_EMPLOYEES))]


_REFRESH_ERRORS = {
    409: "Сессия Jira истекла или отказ Jira (поля state/code/message)",
    403: "Нет прав в Jira",
    502: "Jira отклонила запрос; message - текст Jira как есть",
    503: "Jira недоступна",
}


@router.post(
    "/refresh", response_model=RefreshResult,
    responses={k: {"model": SyncErrorBody, "description": v} for k, v in _REFRESH_ERRORS.items()},
)
def refresh(
    body: RefreshRequest, request: Request, ctx: SessionContext = Depends(require_jira_ok),
) -> RefreshResult:
    """«Обновить» / «Синхронизировать»: немедленный опрос Jira токеном самого пользователя.
    Инкрементальный; при отказе - код ответа и текст Jira как есть, кэш остаётся прежним."""
    service = _service(request)
    res = service.refresh(body.scope, ctx.login)
    if res.code == "busy":
        return RefreshResult(
            scope=body.scope, ok=True, busy=True,
            sync=_stamp(service.sync_info(body.scope)),
        )
    if not res.ok:
        raise _http_error(SyncError(res.code or "error", res.message))
    return RefreshResult(
        scope=body.scope, ok=True, fetched=res.fetched, upserted=res.upserted,
        skipped=res.skipped, sync=_stamp(service.sync_info(body.scope)),
    )


@router.get(
    "/epics/{epic_key}", response_model=EpicOut,
    responses={k: {"model": SyncErrorBody} for k in (403, 409, 502, 503)},
)
def get_epic(
    epic_key: str, request: Request, refresh: bool = False,
    ctx: SessionContext = Depends(authenticated),
) -> EpicOut:
    """Эпик клиента с правилами и условиями (FR-015) при открытии: кэш 1 час; при отказе Jira
    и наличии кэша - кэш с ``stale = true`` и ``error``; без кэша - код отказа и текст Jira."""
    service = _service(request)
    try:
        view = service.get_epic(epic_key, ctx.login, force=refresh)
    except SyncError as err:
        raise _http_error(err) from None
    error = None
    if view.error is not None:
        e = _http_error(view.error).detail
        error = SyncErrorBody(**e) if isinstance(e, dict) else None
    return EpicOut(
        epic_key=view.epic_key, name=view.name, summary=view.summary,
        rules=[RuleOut(**r) for r in view.rules], fetched_at=view.fetched_at,
        stale=view.stale, error=error, needs_total=view.needs_total,
    )
