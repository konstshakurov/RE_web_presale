"""API шлюза LLM (FR-023, FR-030, NFR-010): задачи, предпросмотр, подтверждение; настройки
провайдеров и словарь обезличивания - только администратору.

Ключи провайдеров принимаются на запись и обратно не отдаются никогда (только ``has_key``).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..auth.deps import SessionContext, authenticated, authenticated_csrf, require_admin
from .anonymize import AnonymizationError, Dictionaries
from .dictionaries import (
    MAX_TECH_TERM_LEN,
    MAX_TECH_TERMS,
    MAX_TERM_LEN,
    MAX_TERMS,
    DictionarySource,
)
from .providers import ProviderError, ProviderService
from .repo import LlmRepo, ProviderRow
from .service import GatewayError, LeakError, LlmGateway, TaskView

log = logging.getLogger(__name__)

router = APIRouter(
    tags=["llm"],
    responses={401: {"description": "Требуется вход"}, 403: {"description": "CSRF или права"}},
)

NOT_CONFIGURED = "Шлюз LLM не настроен"

_GATEWAY_STATUS = {
    "task_not_found": 404, "provider_not_found": 404, "no_default_provider": 409,
    "not_awaiting_confirmation": 409, "not_cancellable": 409, "not_retryable": 409,
    "not_editable": 409, "bad_prompt_length": 422,
    "empty_prompt": 422, "prompt_too_long": 413,
}
_PROVIDER_STATUS = {
    "not_found": 404, "name_taken": 409, "default_required": 409, "is_default": 409,
    "in_use": 409,
}

Term = Annotated[str, Field(min_length=1, max_length=MAX_TERM_LEN)]


# --- модели ---

class ProviderPublic(BaseModel):
    id: int
    name: str
    is_external: bool = Field(description="true - текст обезличивается и требует подтверждения")
    is_default: bool


class ProviderOut(ProviderPublic):
    url: str
    timeout_sec: int
    model: str
    has_key: bool = Field(description="Ключ задан (значение не возвращается никогда)")
    updated_at: datetime


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=8, max_length=512,
                     description="Базовый URL OpenAI-совместимого API")
    is_external: bool = True
    api_key: str | None = Field(None, max_length=4096, repr=False)
    timeout_sec: int = Field(120, ge=1, le=3600)
    model: str = Field("", max_length=128)
    is_default: bool = False


class ProviderPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    url: str | None = Field(None, min_length=8, max_length=512)
    is_external: bool | None = None
    api_key: str | None = Field(None, min_length=1, max_length=4096, repr=False)
    remove_key: bool = False
    timeout_sec: int | None = Field(None, ge=1, le=3600)
    model: str | None = Field(None, max_length=128)
    is_default: bool | None = None


class DictionaryBody(BaseModel):
    persons: list[Term] = []
    companies: list[Term] = []
    projects: list[Term] = []
    locations: list[Term] = []


class TechTermsBody(BaseModel):
    terms: list[str] = Field(
        [], max_length=MAX_TECH_TERMS, description="Технологии, которые не обезличиваются")


class TechTermsOut(BaseModel):
    terms: list[str]
    overlaps: list[str] = Field(
        description="Термины, которые есть и в словаре обезличивания: приоритет у словаря, "
                    "они продолжают маскироваться")


class TaskCreate(BaseModel):
    purpose: str = Field(pattern=r"^[a-z0-9_]{1,64}$", description="Код сценария: match, chat...")
    prompt: str = Field(min_length=1, max_length=200_000)
    system: str | None = Field(None, max_length=20_000)
    provider_id: int | None = Field(None, description="Пусто - провайдер по умолчанию")
    ref: str | None = Field(None, max_length=128, description="Ключ потребности/сотрудника")
    terms: DictionaryBody | None = Field(
        None, description="Дополнительные названия/ФИО для обезличивания именно этого запроса")


class PreviewOut(BaseModel):
    system: str | None
    prompt: str = Field(description="Ровно тот текст, который уйдёт во внешнюю LLM")
    replacements: dict[str, int] = Field(description="вид -> число уникальных подстановок")
    ner_used: bool


class TaskOut(BaseModel):
    id: str
    purpose: str
    ref: str | None
    status: str = Field(
        description="awaiting_confirmation | queued | running | done | failed | timeout "
        "| cancelled")
    provider_id: int
    provider_name: str
    is_external: bool
    requires_confirmation: bool = Field(
        description="true - покажите предпросмотр и запросите подтверждение (POST .../confirm)")
    preview: PreviewOut | None = None
    result: str | None = Field(None, description="Ответ LLM после обратной подстановки")
    error_code: str | None = None
    error_message: str | None = Field(None, description="timeout - показать с кнопкой «Повторить»")
    timeout_sec: int
    attempts: int
    created_at: datetime
    finished_at: datetime | None = None
    editable: bool = Field(
        False, description="true - внешний запрос ещё не подтверждён, текст предпросмотра "
                           "можно править")
    edited: bool = Field(False, description="Текст предпросмотра изменён пользователем")


def _provider_out(p: ProviderRow) -> ProviderOut:
    return ProviderOut(
        id=p.id, name=p.name, is_external=p.is_external, is_default=p.is_default, url=p.url,
        timeout_sec=p.timeout_sec, model=p.model, has_key=p.has_key, updated_at=p.updated_at,
    )


def _task_out(t: TaskView) -> TaskOut:
    pv = t.preview
    return TaskOut(
        id=t.id, purpose=t.purpose, ref=t.ref, status=t.status, provider_id=t.provider_id,
        provider_name=t.provider_name, is_external=t.is_external,
        requires_confirmation=t.requires_confirmation,
        preview=PreviewOut(system=pv.system, prompt=pv.prompt, replacements=pv.replacements,
                           ner_used=pv.ner_used) if pv else None,
        result=t.result, error_code=t.error_code, error_message=t.error_message,
        timeout_sec=t.timeout_sec, attempts=t.attempts, created_at=t.created_at,
        finished_at=t.finished_at, editable=t.editable, edited=bool(pv and pv.edited),
    )


def _state(request: Request, name: str):
    obj = getattr(request.app.state, name, None)
    if obj is None:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    return obj


def _gateway(request: Request) -> LlmGateway:
    return _state(request, "llm")


def _providers(request: Request) -> ProviderService:
    return _state(request, "llm_providers")


def _repo(request: Request) -> LlmRepo:
    return _state(request, "llm_repo")


def _dict_source(request: Request) -> DictionarySource:
    return _state(request, "llm_dictionary")


def _gw_error(exc: GatewayError) -> HTTPException:
    if isinstance(exc, LeakError):
        return HTTPException(status_code=422, detail={
            "code": exc.code, "message": exc.message, "fragments": exc.fragments})
    return HTTPException(status_code=_GATEWAY_STATUS.get(exc.code, 400), detail=exc.message)


def _prov_error(exc: ProviderError) -> HTTPException:
    return HTTPException(status_code=_PROVIDER_STATUS.get(exc.code, 422), detail=exc.message)


# --- провайдеры: список выбора для всех, настройки - админу ---

@router.get("/llm/providers", response_model=list[ProviderPublic])
def list_public_providers(
    request: Request, _: SessionContext = Depends(authenticated)
) -> list[ProviderPublic]:
    """Для выбора модели пользователем (FR-017): без URL, без сведений о ключах."""
    return [ProviderPublic(id=p.id, name=p.name, is_external=p.is_external,
                           is_default=p.is_default) for p in _repo(request).list_providers()]


@router.get("/admin/llm/providers", response_model=list[ProviderOut])
def list_providers(
    request: Request, _: SessionContext = Depends(require_admin)
) -> list[ProviderOut]:
    return [_provider_out(p) for p in _repo(request).list_providers()]


@router.post("/admin/llm/providers", response_model=ProviderOut, status_code=201)
def create_provider(
    body: ProviderCreate, request: Request, ctx: SessionContext = Depends(require_admin)
) -> ProviderOut:
    try:
        p = _providers(request).create(
            body.name, body.url, is_external=body.is_external, api_key=body.api_key,
            timeout_sec=body.timeout_sec, model=body.model, is_default=body.is_default,
        )
    except ProviderError as exc:
        raise _prov_error(exc) from None
    log.info("LLM: %s создал провайдера %s", ctx.login, p.id, extra={"external": p.is_external})
    return _provider_out(p)


@router.patch("/admin/llm/providers/{provider_id}", response_model=ProviderOut)
def patch_provider(
    provider_id: int, body: ProviderPatch, request: Request,
    ctx: SessionContext = Depends(require_admin),
) -> ProviderOut:
    """Смена URL, ключа, таймаута, модели, провайдера по умолчанию (FR-030, NFR-010)."""
    try:
        p = _providers(request).update(
            provider_id, name=body.name, url=body.url, is_external=body.is_external,
            timeout_sec=body.timeout_sec, model=body.model, is_default=body.is_default,
            api_key=body.api_key, remove_key=body.remove_key,
        )
    except ProviderError as exc:
        raise _prov_error(exc) from None
    log.info("LLM: %s изменил провайдера %s", ctx.login, provider_id,
             extra={"key_changed": bool(body.api_key or body.remove_key)})
    return _provider_out(p)


@router.delete("/admin/llm/providers/{provider_id}", status_code=204)
def delete_provider(
    provider_id: int, request: Request, ctx: SessionContext = Depends(require_admin)
) -> Response:
    try:
        _providers(request).delete(provider_id)
    except ProviderError as exc:
        raise _prov_error(exc) from None
    log.info("LLM: %s удалил провайдера %s", ctx.login, provider_id)
    return Response(status_code=204)


# --- словарь обезличивания (админ) ---

def _clean_terms(items: list[str]) -> tuple[str, ...]:
    return tuple(" ".join(i.split()) for i in items if i.strip())


def _dictionaries(body: DictionaryBody) -> Dictionaries:
    return Dictionaries(
        persons=_clean_terms(body.persons), companies=_clean_terms(body.companies),
        projects=_clean_terms(body.projects), locations=_clean_terms(body.locations),
    )


def _dictionary_body(d: Dictionaries) -> DictionaryBody:
    return DictionaryBody(persons=list(d.persons), companies=list(d.companies),
                          projects=list(d.projects), locations=list(d.locations))


@router.get("/admin/llm/dictionary", response_model=DictionaryBody)
def get_dictionary(
    request: Request, _: SessionContext = Depends(require_admin)
) -> DictionaryBody:
    return _dictionary_body(_dict_source(request).load_custom())


@router.put("/admin/llm/dictionary", response_model=DictionaryBody)
def put_dictionary(
    body: DictionaryBody, request: Request, ctx: SessionContext = Depends(require_admin)
) -> DictionaryBody:
    """Дополнительные строки обезличивания (клиенты, проекты, ФИО, локации); заменяет список."""
    d = _dictionaries(body)
    total = len(d.persons) + len(d.companies) + len(d.projects) + len(d.locations)
    if total > MAX_TERMS:
        raise HTTPException(status_code=422, detail=f"Не более {MAX_TERMS} строк")
    _dict_source(request).save_custom(d)
    log.info("LLM: %s обновил словарь обезличивания (%s строк)", ctx.login, total)
    return _dictionary_body(d)


# --- технологии, которые не обезличиваются (KON-73, админ) ---

def _tech_terms(items: list[str]) -> list[str]:
    """trim и схлопывание пробелов, дубли без учёта регистра убираются (первое написание)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        t = " ".join(raw.split())
        if not t:
            raise HTTPException(status_code=422, detail="Пустая строка в списке технологий")
        if len(t) > MAX_TECH_TERM_LEN:
            raise HTTPException(
                status_code=422,
                detail=f"Название технологии не длиннее {MAX_TECH_TERM_LEN} знаков")
        key = t.casefold()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def _tech_out(src: DictionarySource, terms: list[str]) -> TechTermsOut:
    return TechTermsOut(terms=terms, overlaps=src.tech_overlaps(terms))


@router.get("/admin/llm/tech-terms", response_model=TechTermsOut)
def get_tech_terms(
    request: Request, _: SessionContext = Depends(require_admin)
) -> TechTermsOut:
    """Дополнительные технологии (к встроенному списку), которые не заменяются на [КОМПАНИЯ_n]."""
    src = _dict_source(request)
    return _tech_out(src, list(src.load_tech_terms()))


@router.put("/admin/llm/tech-terms", response_model=TechTermsOut)
def put_tech_terms(
    body: TechTermsBody, request: Request, ctx: SessionContext = Depends(require_admin)
) -> TechTermsOut:
    """Заменяет список. Пустой список допустим (остаётся встроенный). Действует сразу."""
    terms = _tech_terms(body.terms)
    src = _dict_source(request)
    src.save_tech_terms(terms)
    log.info("LLM: %s обновил список технологий (%s строк)", ctx.login, len(terms))
    return _tech_out(src, terms)


# --- задачи ---

@router.post("/llm/tasks", response_model=TaskOut, status_code=201)
async def create_task(
    body: TaskCreate, request: Request, ctx: SessionContext = Depends(authenticated_csrf),
) -> TaskOut:
    """Поставить запрос к LLM. Для внешнего провайдера ответ содержит предпросмотр и
    ``requires_confirmation=true``: без ``POST /llm/tasks/{id}/confirm`` отправки не будет."""
    gw = _gateway(request)
    terms = _dictionaries(body.terms) if body.terms is not None else None
    try:
        view = await run_in_threadpool(
            gw.submit, ctx.login, body.purpose, body.prompt, system=body.system,
            provider_id=body.provider_id, ref=body.ref, terms=terms,
        )
    except GatewayError as exc:
        raise _gw_error(exc) from None
    except AnonymizationError:
        raise HTTPException(
            status_code=422,
            detail="Не удалось надёжно обезличить текст; отправка во внешнюю LLM запрещена",
        ) from None
    return _task_out(view)


@router.get("/llm/tasks", response_model=list[TaskOut])
def list_tasks(
    request: Request, ctx: SessionContext = Depends(authenticated),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TaskOut]:
    return [_task_out(t) for t in _gateway(request).list_tasks(ctx.login, limit)]


def _call(fn, *args) -> TaskOut:
    try:
        return _task_out(fn(*args))
    except GatewayError as exc:
        raise _gw_error(exc) from None


@router.get("/llm/tasks/{task_id}", response_model=TaskOut)
def get_task(
    task_id: str, request: Request, ctx: SessionContext = Depends(authenticated)
) -> TaskOut:
    """Статус, предпросмотр, результат. Опрос с интервалом, UI не блокируется (ADR-007)."""
    return _call(_gateway(request).get, ctx.login, task_id)


@router.post("/llm/tasks/{task_id}/confirm", response_model=TaskOut)
def confirm_task(
    task_id: str, request: Request, ctx: SessionContext = Depends(authenticated_csrf)
) -> TaskOut:
    """Подтвердить отправку показанного предпросмотра; задача уходит в очередь."""
    return _call(_gateway(request).confirm, ctx.login, task_id)


@router.post("/llm/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel_task(
    task_id: str, request: Request, ctx: SessionContext = Depends(authenticated_csrf)
) -> TaskOut:
    return _call(_gateway(request).cancel, ctx.login, task_id)


@router.post("/llm/tasks/{task_id}/retry", response_model=TaskOut)
def retry_task(
    task_id: str, request: Request, ctx: SessionContext = Depends(authenticated_csrf)
) -> TaskOut:
    """Повтор после таймаута (NFR-010) или ошибки провайдера."""
    return _call(_gateway(request).retry, ctx.login, task_id)
