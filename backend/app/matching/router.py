"""API модуля matching (KON-38): сопоставление потребности с кандидатами (FR-005, FR-036),
профиль сотрудника (FR-003), навыки Основного CV (FR-027), фильтры «Резюме» (FR-032).

Сопоставление асинхронное: ``POST /needs/{key}/match`` ставит задачи в шлюз LLM и сразу
возвращает состояние; результат опрашивается ``GET`` и берётся из кэша.

Оценка двухступенчатая: скрининг всех кандидатов по навыкам, затем глубокая оценка топ-N по
тексту CV; следующие порции - ``POST /needs/{key}/match/next``. Внешний провайдер: каждая ступень
- прогон (``run_id``) со своим предпросмотром всех пакетов (``GET /matching/runs/{run_id}``) и
одним подтверждением (``POST /matching/runs/{run_id}/confirm``); подтверждение отдельных задач
``POST /llm/tasks/{id}/confirm`` остаётся доступным.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from ..auth.deps import SessionContext, authenticated, authenticated_csrf, require_admin
from ..cv.repo import CvRepo
from ..llm_gateway import AnonymizationError, GatewayError, LeakError
from .profile import guess_years, parse_sections
from .screening import (
    DEEP_SIZE_MAX,
    DEEP_SIZE_MIN,
    SCREEN_BATCH_MAX,
    SCREEN_BATCH_MIN,
)
from .semantic import MAX_QUERY_CHARS, MIN_QUERY_CHARS
from .service import (
    MatchingError,
    MatchingService,
    MatchState,
    RunInfo,
    SearchFilters,
    SearchState,
)

log = logging.getLogger(__name__)

router = APIRouter(
    tags=["matching"],
    responses={401: {"description": "Требуется вход"}, 403: {"description": "CSRF или права"}},
)

NO_STORE_MSG = "Хранилище CV не настроено (CV_DB_URL)"
NEED_KEY = Path(pattern=r"^[A-Z][A-Z0-9_]*-[0-9]+$", description="Ключ потребности Jira")
EMPLOYEE_KEY = Path(pattern=r"^[A-Z][A-Z0-9_]*-[0-9]+$", description="Ключ карточки сотрудника")

_GATEWAY_STATUS = {
    "provider_not_found": 404, "no_default_provider": 409, "empty_prompt": 422,
    "prompt_too_long": 413, "task_not_found": 404, "not_awaiting_confirmation": 409,
    "not_editable": 409, "bad_prompt_length": 422,
}
_MATCHING_STATUS = {
    "need_not_found": 404, "employee_not_found": 404, "bad_query": 422, "run_not_found": 404,
    "bad_settings": 422, "task_not_found": 404}
RUN_ID = Path(pattern=r"^[0-9a-fA-F-]{36}$", description="Идентификатор прогона (run_id)")


# --- модели ---

class MatchRequest(BaseModel):
    provider_id: int | None = Field(None, description="Провайдер LLM; пусто - по умолчанию")
    force: bool = Field(
        False, description="Пересчитать, даже если результат в кэше («Пересчитать»)")


class NextRequest(BaseModel):
    provider_id: int | None = Field(None, description="Провайдер LLM; пусто - по умолчанию")


class TaskOut(BaseModel):
    task_id: str = Field(description="Задача шлюза LLM: GET /llm/tasks/{id}, confirm, retry")
    status: str = Field(description="Статус задачи шлюза либо итог пакета: done | failed")
    requires_confirmation: bool = Field(
        description="true - внешний провайдер, нужен предпросмотр и confirm владельцем")
    batch_status: str = Field(description="pending | done | failed")
    candidates: int
    stage: str = Field("deep", description="screening - скрининг по навыкам; deep - по тексту CV")
    run_id: str = Field(
        "", description="Прогон: GET /matching/runs/{run_id} - предпросмотр всех пакетов, "
                        "POST .../confirm - одно подтверждение на прогон")


class MatchItemOut(BaseModel):
    employee_key: str
    full_name: str
    status: str = Field(description="Статус сотрудника в Jira")
    percent: int = Field(ge=0, le=100)
    rationale: str = Field(description="Обоснование по клику на %")
    note: str | None = Field(description="Заметка о сотруднике (значок, FR-033)")
    note_author: str | None
    note_updated_at: datetime | None
    cv_file_id: int = Field(description="Основное CV: кнопка «Резюме» (FR-037)")
    calculated_at: datetime
    stale: bool = Field(description="Потребность, заметка или модель изменились после расчёта")
    skills: list[str]


class MatchStateOut(BaseModel):
    need_key: str
    status: str = Field(
        description="none | running | awaiting_confirmation | done | partial | failed")
    requested_by: str | None
    model: str | None
    started_at: datetime | None
    calculated_at: datetime | None = Field(description="Дата расчёта для показа в списке (FR-036)")
    stale: bool
    candidates_total: int = Field(description="Допустимые к назначению с основным CV")
    evaluated: int
    total: int = Field(description="Размер выдачи; items - срез limit/offset («Показать ещё»)")
    items: list[MatchItemOut]
    tasks: list[TaskOut]
    batches_total: int
    batches_done: int
    batches_failed: int
    errors: list[str] = Field(description="Причины отказов пакетов, без текстов CV")
    stage: str | None = Field(
        None, description="Ступень текущего прогона: screening | deep; null - прогона нет")
    run_id: str | None = Field(
        None, description="Текущий прогон: при status=awaiting_confirmation - его предпросмотр "
                          "и подтверждение (GET/POST /matching/runs/{run_id}...)")
    deep_size: int = Field(0, description="Порция глубокой оценки N (настройка администратора)")
    remaining: int = Field(0, description="Допустимые, ещё не оценённые глубоко")
    screened: int = Field(0, description="Кандидаты со свежим баллом скрининга LLM")
    screen_total: int = Field(0, description="Допустимые с выделенными навыками")
    can_evaluate_next: bool = Field(
        False, description="Можно запустить POST /needs/{key}/match/next (есть кого оценивать, "
                           "прогона нет, провайдер настроен)")


class SearchRequest(BaseModel):
    query: str = Field(min_length=MIN_QUERY_CHARS, max_length=MAX_QUERY_CHARS,
                       description="Запрос на естественном языке")
    provider_id: int | None = Field(None, description="Провайдер LLM; пусто - по умолчанию")
    force: bool = Field(False, description="Пересчитать, даже если оценки в кэше")
    status: str | None = Field(
        None, max_length=128, description=(
            "Фильтр «Статус сотрудника»; без него - все статусы, кроме Коммерческий проект, "
            "Уволен, Декрет, Недоступен для подключения (FR-002)"))
    technology: list[str] = Field(default_factory=list, description="Фильтр «Технология»")
    match: str = Field("all", pattern="^(all|any)$")
    q: str | None = Field(None, max_length=200, description="Фильтр «Поиск» (текстовый)")
    limit: int = Field(10, ge=1, le=100)


class SearchItemOut(BaseModel):
    employee_key: str
    full_name: str
    status: str
    source: str = Field(
        description="llm - глубокая оценка LLM; screening - предварительная оценка по навыкам; "
                    "fallback - по технологиям и тексту")
    percent: int | None = Field(None, ge=0, le=100, description="Только у source=llm")
    screen_percent: int | None = Field(
        None, ge=0, le=100, description="Предварительная оценка скрининга по навыкам (LLM)")
    rationale: str = Field(description="Обоснование LLM либо перечень совпадений без LLM")
    score: int = Field(description="Детерминированная оценка: технологии * 3 + слова запроса")
    matched_skills: list[str]
    matched_terms: list[str]
    note: str | None
    note_author: str | None
    note_updated_at: datetime | None
    cv_file_id: int = Field(description="Основное CV: кнопка «Резюме» (FR-037)")
    calculated_at: datetime | None
    stale: bool = Field(description="Заметка или модель изменились после расчёта")
    skills: list[str]


class SearchStateOut(BaseModel):
    query: str
    status: str = Field(
        description="none | running | awaiting_confirmation | done | partial | failed | "
                    "unavailable (LLM недоступна)")
    mode: str = Field(description="llm - есть оценки LLM; fallback - только технологии и текст")
    notice: str | None = Field(description="Понятное сообщение о деградации без LLM")
    requested_by: str | None
    model: str | None
    started_at: datetime | None
    calculated_at: datetime | None
    stale: bool
    candidates_total: int = Field(description="В области поиска с учётом фильтров")
    evaluated: int = Field(description="С оценкой LLM")
    total: int = Field(description="Размер выдачи; items - срез limit/offset")
    items: list[SearchItemOut]
    tasks: list[TaskOut]
    batches_total: int
    batches_done: int
    batches_failed: int
    errors: list[str]
    stage: str | None = Field(None, description="screening | deep; null - прогона нет")
    run_id: str | None = Field(None, description="Текущий прогон (подтверждение внешней LLM)")
    deep_size: int = Field(0, description="Порция глубокой оценки N (настройка администратора)")
    remaining: int = Field(0, description="В выдаче без глубокой оценки")
    screened: int = Field(0, description="Со свежим баллом скрининга LLM")
    screen_total: int = Field(0, description="В области поиска с выделенными навыками")
    can_evaluate_next: bool = Field(
        False, description="Можно запустить POST /resume/semantic-search/next")


class RunTaskOut(BaseModel):
    task_id: str
    status: str
    candidates: int
    requires_confirmation: bool
    system: str | None = Field(None, description="Обезличенный системный запрос (внешняя LLM)")
    prompt: str | None = Field(None, description="Ровно тот текст, который уйдёт во внешнюю LLM")
    replacements: dict[str, int] = Field(description="вид -> число уникальных подстановок")
    ner_used: bool
    error_message: str | None = None
    editable: bool = Field(
        False, description="true - текст prompt можно править: PUT .../tasks/{task_id}/prompt")
    edited: bool = Field(False, description="prompt изменён пользователем (показан актуальный)")


class PromptIn(BaseModel):
    prompt: str = Field(
        min_length=1, max_length=200_000,
        description="Новый обезличенный текст запроса: не длиннее 2 длин исходного")


class RunOut(BaseModel):
    run_id: str
    kind: str = Field(description="match | search")
    stage: str = Field(description="screening | deep")
    status: str = Field(
        description="awaiting_confirmation | running | done | partial | failed")
    requires_confirmation: bool = Field(
        description="true - покажите предпросмотр tasks[] и запросите подтверждение")
    is_external: bool
    provider_name: str
    candidates: int = Field(description="Кандидатов во всех пакетах прогона")
    tasks: list[RunTaskOut] = Field(description="Предпросмотр: все пакеты прогона, обезличенные")
    replacements: dict[str, int] = Field(description="Подстановки по всему прогону")
    need_key: str | None = None


class MatchSettingsOut(BaseModel):
    deep_size: int = Field(description="N: сколько кандидатов оценивается глубоко за прогон")
    deep_size_min: int
    deep_size_max: int
    screen_batch: int = Field(description="Кандидатов в пакете скрининга")
    screen_batch_min: int
    screen_batch_max: int


class MatchSettingsIn(BaseModel):
    deep_size: int | None = Field(None, ge=DEEP_SIZE_MIN, le=DEEP_SIZE_MAX)
    screen_batch: int | None = Field(None, ge=SCREEN_BATCH_MIN, le=SCREEN_BATCH_MAX)


class SkillsOut(BaseModel):
    employee_key: str
    outcome: str = Field(description="scheduled | exists | no_cv | no_provider | error")
    task_id: str | None = None
    message: str | None = None


class SkillsBatchOut(BaseModel):
    items: list[SkillsOut]
    scheduled: int


class ResumeItem(BaseModel):
    employee_key: str
    full_name: str
    status: str
    assignable: bool
    description: str | None = Field(description="«Подробнее об опыте» из Jira (FR-002)")
    vacation_plan: str | None
    note: str | None
    note_author: str | None
    note_updated_at: datetime | None
    main_cv_file_id: int | None
    main_cv_file_name: str | None
    extraction_status: str | None = Field(description="ok | error | none; null - нет основного CV")
    skills: list[str]
    skills_status: str | None = Field(description="pending | ok | failed; null - не извлекались")


class ResumeList(BaseModel):
    items: list[ResumeItem]
    total: int


class TechnologyOut(BaseModel):
    tag: str
    employees: int


class FileLink(BaseModel):
    id: int
    kind: str
    file_name: str


class ProfileOut(BaseModel):
    employee_key: str
    full_name: str
    status: str
    assignable: bool
    description: str | None
    vacation_plan: str | None
    note: str | None
    note_author: str | None
    note_updated_at: datetime | None
    main_cv: FileLink | None
    files: list[FileLink] = Field(description="Ссылки на все актуальные CV сотрудника")
    skills: list[str] = Field(description="Технологии и навыки из Основного CV (FR-027)")
    skills_status: str | None
    sections: dict[str, str] = Field(
        description="Разделы корпоративного CV (BR-007): profile, stack, industries, skills, "
                    "languages, education, projects; пусто, если заголовков в CV нет")
    experience_years: int | None = Field(description="Только если CV называет годы явно")
    synced_at: datetime


# --- помощники ---

def _service(request: Request) -> MatchingService:
    svc: MatchingService | None = getattr(request.app.state, "matching", None)
    if svc is None:
        raise HTTPException(status_code=503, detail=NO_STORE_MSG)
    return svc


def _cv(request: Request) -> CvRepo:
    repo = getattr(request.app.state, "cv", None)
    if repo is None:
        raise HTTPException(status_code=503, detail=NO_STORE_MSG)
    return repo


def _tasks_out(tasks) -> list[TaskOut]:
    return [TaskOut(task_id=t.task_id, status=t.status,
                    requires_confirmation=t.requires_confirmation, batch_status=t.batch_status,
                    candidates=t.candidates, stage=t.stage, run_id=t.run_id) for t in tasks]


def _run_out(r: RunInfo) -> RunOut:
    return RunOut(
        run_id=r.run_id, kind=r.kind, stage=r.stage, status=r.status,
        requires_confirmation=r.requires_confirmation, is_external=r.is_external,
        provider_name=r.provider_name, candidates=r.candidates, replacements=r.replacements,
        need_key=r.need_key,
        tasks=[RunTaskOut(
            task_id=t.task_id, status=t.status, candidates=t.candidates,
            requires_confirmation=t.requires_confirmation, system=t.system, prompt=t.prompt,
            replacements=t.replacements, ner_used=t.ner_used, error_message=t.error_message,
            editable=t.editable, edited=t.edited)
            for t in r.tasks])


def _settings_out(deep: int, screen: int) -> MatchSettingsOut:
    return MatchSettingsOut(
        deep_size=deep, deep_size_min=DEEP_SIZE_MIN, deep_size_max=DEEP_SIZE_MAX,
        screen_batch=screen, screen_batch_min=SCREEN_BATCH_MIN, screen_batch_max=SCREEN_BATCH_MAX)


def _state_out(s: MatchState) -> MatchStateOut:
    return MatchStateOut(
        need_key=s.need_key, status=s.status, requested_by=s.requested_by, model=s.model,
        started_at=s.started_at, calculated_at=s.calculated_at, stale=s.stale,
        candidates_total=s.candidates_total, evaluated=s.evaluated, total=s.total,
        items=[MatchItemOut(
            employee_key=i.employee_key, full_name=i.full_name, status=i.status,
            percent=i.percent, rationale=i.rationale, note=i.note, note_author=i.note_author,
            note_updated_at=i.note_updated_at, cv_file_id=i.cv_file_id,
            calculated_at=i.calculated_at, stale=i.stale, skills=list(i.skills))
            for i in s.items],
        tasks=_tasks_out(s.tasks),
        batches_total=s.batches_total, batches_done=s.batches_done,
        batches_failed=s.batches_failed, errors=s.errors, stage=s.stage, run_id=s.run_id,
        deep_size=s.deep_size, remaining=s.remaining, screened=s.screened,
        screen_total=s.screen_total, can_evaluate_next=s.can_evaluate_next,
    )


def _search_out(s: SearchState) -> SearchStateOut:
    return SearchStateOut(
        query=s.query, status=s.status, mode=s.mode, notice=s.notice,
        requested_by=s.requested_by, model=s.model, started_at=s.started_at,
        calculated_at=s.calculated_at, stale=s.stale, candidates_total=s.candidates_total,
        evaluated=s.evaluated, total=s.total,
        items=[SearchItemOut(
            employee_key=i.employee_key, full_name=i.full_name, status=i.status, source=i.source,
            percent=i.percent, rationale=i.rationale, score=i.score,
            matched_skills=list(i.matched_skills), matched_terms=list(i.matched_terms),
            note=i.note, note_author=i.note_author, note_updated_at=i.note_updated_at,
            cv_file_id=i.cv_file_id, calculated_at=i.calculated_at, stale=i.stale,
            skills=list(i.skills), screen_percent=i.screen_percent) for i in s.items],
        tasks=_tasks_out(s.tasks),
        batches_total=s.batches_total, batches_done=s.batches_done,
        batches_failed=s.batches_failed, errors=s.errors, stage=s.stage, run_id=s.run_id,
        deep_size=s.deep_size, remaining=s.remaining, screened=s.screened,
        screen_total=s.screen_total, can_evaluate_next=s.can_evaluate_next)


def _filters(status: str | None, technology: list[str] | None, match: str,
             text: str | None) -> SearchFilters:
    tags = tuple(sorted({t.strip().lower() for t in (technology or []) if t.strip()}))
    return SearchFilters(status=(status or "").strip() or None, tags=tags,
                         match_all=match == "all", text=(text or "").strip() or None)


def _raise(exc: Exception) -> None:
    if isinstance(exc, LeakError):
        raise HTTPException(status_code=422, detail={
            "code": exc.code, "message": exc.message, "fragments": exc.fragments}) from exc
    if isinstance(exc, MatchingError):
        raise HTTPException(
            status_code=_MATCHING_STATUS.get(exc.code, 400), detail=exc.message) from exc
    if isinstance(exc, GatewayError):
        raise HTTPException(
            status_code=_GATEWAY_STATUS.get(exc.code, 400), detail=exc.message) from exc
    if isinstance(exc, AnonymizationError):
        raise HTTPException(
            status_code=422,
            detail="Не удалось надёжно обезличить текст для внешней LLM; отправка отменена",
        ) from exc
    raise exc


# --- сопоставление (FR-005, FR-036) ---

@router.post(
    "/needs/{need_key}/match", response_model=MatchStateOut, status_code=202,
    responses={404: {"description": "Потребность не найдена в кэше"},
               409: {"description": "Провайдер LLM по умолчанию не настроен"},
               422: {"description": "Обезличивание не удалось"}},
)
def start_match(
    request: Request, need_key: Annotated[str, NEED_KEY], body: MatchRequest | None = None,
    ctx: SessionContext = Depends(authenticated_csrf),
) -> MatchStateOut:
    """«Сопоставить» / «Пересчитать»: детерминированный отбор, затем оценка LLM пакетами.
    Результат уже в кэше (потребность, CV, заметка и модель не менялись) - LLM не вызывается."""
    body = body or MatchRequest()
    try:
        state = _service(request).start_match(
            need_key, ctx.login, provider_id=body.provider_id, force=body.force)
    except (MatchingError, GatewayError, AnonymizationError) as exc:
        _raise(exc)
    log.info("matching: %s запустил сопоставление %s", ctx.login, need_key)
    return _state_out(state)


@router.get("/needs/{need_key}/match", response_model=MatchStateOut,
            responses={404: {"description": "Потребность не найдена в кэше"}})
def get_match(
    request: Request, need_key: Annotated[str, NEED_KEY],
    limit: int = Query(10, ge=1, le=100, description="Первые 10; «Показать ещё» - offset"),
    offset: int = Query(0, ge=0),
    _: SessionContext = Depends(authenticated),
) -> MatchStateOut:
    """Состояние и сохранённый результат (повторный просмотр без вызова LLM)."""
    try:
        return _state_out(_service(request).get_state(need_key, limit=limit, offset=offset))
    except MatchingError as exc:
        _raise(exc)


@router.post(
    "/needs/{need_key}/match/next", response_model=MatchStateOut, status_code=202,
    responses={404: {"description": "Потребность не найдена в кэше"},
               409: {"description": "Провайдер LLM по умолчанию не настроен"},
               422: {"description": "Обезличивание не удалось"}},
)
def evaluate_next(
    request: Request, need_key: Annotated[str, NEED_KEY], body: NextRequest | None = None,
    ctx: SessionContext = Depends(authenticated_csrf),
) -> MatchStateOut:
    """«Оценить следующих»: глубокая оценка следующей порции (``deep_size``) рейтинга скрининга.
    Уже оценённые (кэш по потребности, CV и модели) не пересчитываются. Отдельный прогон
    (``run_id``): для внешнего провайдера - отдельный предпросмотр и подтверждение."""
    body = body or NextRequest()
    try:
        state = _service(request).evaluate_next(need_key, ctx.login, provider_id=body.provider_id)
    except (MatchingError, GatewayError, AnonymizationError) as exc:
        _raise(exc)
    log.info("matching: %s запустил оценку следующих %s", ctx.login, need_key)
    return _state_out(state)


# --- «Поиск по смыслу (LLM)» (FR-032) ---

@router.post(
    "/resume/semantic-search", response_model=SearchStateOut, status_code=202,
    responses={404: {"description": "Провайдер не найден"},
               422: {"description": "Запрос слишком короткий или длинный"}},
)
def start_semantic_search(
    request: Request, body: SearchRequest, ctx: SessionContext = Depends(authenticated_csrf),
) -> SearchStateOut:
    """Детерминированный отбор по статусам, основному CV, технологиям и тексту, затем итоговое
    ранжирование LLM пакетами. Результат в кэше (запрос + CV + модель) - LLM не вызывается.
    Без LLM (нет провайдера, ошибка, таймаут) список строится по технологиям и тексту, причина
    в ``notice``. Внешний провайдер: обезличивание и подтверждение по FR-023."""
    try:
        state = _service(request).start_search(
            ctx.login, body.query, provider_id=body.provider_id, force=body.force,
            filters=_filters(body.status, body.technology, body.match, body.q), limit=body.limit)
    except (MatchingError, GatewayError) as exc:
        _raise(exc)
    log.info("matching: %s запустил поиск по смыслу", ctx.login)  # текст запроса в лог не пишем
    return _search_out(state)


@router.post(
    "/resume/semantic-search/next", response_model=SearchStateOut, status_code=202,
    responses={409: {"description": "Провайдер LLM по умолчанию не настроен"},
               422: {"description": "Запрос слишком короткий или длинный"}},
)
def evaluate_next_search(
    request: Request, body: SearchRequest, ctx: SessionContext = Depends(authenticated_csrf),
) -> SearchStateOut:
    """«Оценить следующих» в поиске по смыслу: глубокая оценка следующей порции рейтинга
    (те же ``query`` и фильтры, что у основного запроса). Отдельный прогон - отдельное
    подтверждение у внешнего провайдера."""
    try:
        state = _service(request).evaluate_next_search(
            ctx.login, body.query, provider_id=body.provider_id,
            filters=_filters(body.status, body.technology, body.match, body.q), limit=body.limit)
    except (MatchingError, GatewayError, AnonymizationError) as exc:
        _raise(exc)
    log.info("matching: %s запустил оценку следующих в поиске", ctx.login)
    return _search_out(state)


@router.get("/resume/semantic-search", response_model=SearchStateOut,
            responses={422: {"description": "Запрос слишком короткий или длинный"}})
def get_semantic_search(
    request: Request,
    query: str = Query(..., max_length=MAX_QUERY_CHARS + 200, description="Запрос поиска"),
    status: str | None = Query(None, max_length=128),
    technology: Annotated[list[str] | None, Query()] = None,
    match: str = Query("all", pattern="^(all|any)$"),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0),
    ctx: SessionContext = Depends(authenticated),
) -> SearchStateOut:
    """Состояние прогона и сохранённый результат текущего пользователя по запросу; без прогона
    - ранжирование по технологиям и тексту без обращения к LLM."""
    try:
        return _search_out(_service(request).get_search(
            ctx.login, query, filters=_filters(status, technology, match, q),
            limit=limit, offset=offset))
    except MatchingError as exc:
        _raise(exc)


# --- прогон целиком: один предпросмотр и одно подтверждение (FR-023) ---

@router.get("/matching/runs/{run_id}", response_model=RunOut,
            responses={404: {"description": "Прогон не найден (или не ваш)"}})
def get_run(
    request: Request, run_id: Annotated[str, RUN_ID],
    ctx: SessionContext = Depends(authenticated),
) -> RunOut:
    """Прогон ступени целиком: статус и обезличенный предпросмотр всех пакетов. Внешний
    провайдер: показать ``tasks[].prompt``, затем ``POST .../confirm``."""
    try:
        return _run_out(_service(request).run_info(ctx.login, run_id))
    except (MatchingError, GatewayError) as exc:
        _raise(exc)


@router.post("/matching/runs/{run_id}/confirm", response_model=RunOut,
             responses={404: {"description": "Прогон не найден (или не ваш)"},
                        409: {"description": "Прогон не ожидает подтверждения"}})
def confirm_run(
    request: Request, run_id: Annotated[str, RUN_ID],
    ctx: SessionContext = Depends(authenticated_csrf),
) -> RunOut:
    """Подтвердить отправку показанного предпросмотра всего прогона: все его задачи уходят в
    очередь одной транзакцией - либо ни одна. Без предпросмотра отправки нет (FR-023)."""
    try:
        return _run_out(_service(request).confirm_run(ctx.login, run_id))
    except (MatchingError, GatewayError) as exc:
        _raise(exc)


@router.put(
    "/matching/runs/{run_id}/tasks/{task_id}/prompt", response_model=RunOut,
    responses={404: {"description": "Прогон или задача не найдены (или не ваши)"},
               409: {"description": "Текст уже подтверждён/отправлен или задача внутренняя"},
               422: {"description": "Длина вне 1..2 длин исходного либо в тексте найдены "
                                    "необезличенные фрагменты: detail = {code: leak_detected, "
                                    "message, fragments[]}; текст не сохранён"}})
def edit_task_prompt(
    request: Request, body: PromptIn, run_id: Annotated[str, RUN_ID],
    task_id: Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{36}$", description="Задача прогона")],
    ctx: SessionContext = Depends(authenticated_csrf),
) -> RunOut:
    """Править обезличенный текст, который уйдёт во внешнюю LLM, до подтверждения прогона
    (FR-023). Перед сохранением проверка остатков ПДн повторяется (regex, словари, NER и
    оригиналы подстановок задачи); плейсхолдеры вне таблицы подстановок допустимы. Подтверждение
    и выполнение используют отредактированный текст; повторная правка возможна до подтверждения."""
    try:
        return _run_out(_service(request).edit_task_prompt(
            ctx.login, run_id, task_id, body.prompt))
    except (MatchingError, GatewayError, AnonymizationError) as exc:
        _raise(exc)


@router.post("/matching/runs/{run_id}/cancel", response_model=RunOut,
             responses={404: {"description": "Прогон не найден (или не ваш)"}})
def cancel_run(
    request: Request, run_id: Annotated[str, RUN_ID],
    ctx: SessionContext = Depends(authenticated_csrf),
) -> RunOut:
    """Отменить прогон: неподтверждённые и ожидающие задачи отменяются, собранное сохраняется."""
    try:
        return _run_out(_service(request).cancel_run(ctx.login, run_id))
    except (MatchingError, GatewayError) as exc:
        _raise(exc)


# --- настройки двухступенчатой оценки (администратор) ---

@router.get("/admin/matching/settings", response_model=MatchSettingsOut)
def get_matching_settings(
    request: Request, _: SessionContext = Depends(require_admin),
) -> MatchSettingsOut:
    """Размер глубокой оценки N (по умолчанию 30, 5..100) и размер пакета скрининга (по
    умолчанию 50, 10..100)."""
    return _settings_out(*_service(request).settings())


@router.put("/admin/matching/settings", response_model=MatchSettingsOut,
            responses={422: {"description": "Значение вне границ"}})
def put_matching_settings(
    body: MatchSettingsIn, request: Request, ctx: SessionContext = Depends(require_admin),
) -> MatchSettingsOut:
    """Изменить настройки (передаются только изменяемые поля). Действуют на новые прогоны."""
    try:
        out = _service(request).update_settings(body.deep_size, body.screen_batch)
    except MatchingError as exc:
        _raise(exc)
    log.info("matching: %s изменил настройки оценки: N=%s, пакет скрининга=%s",
             ctx.login, out[0], out[1])
    return _settings_out(*out)


# --- навыки (FR-027) ---

@router.post("/resume/employees/{employee_key}/skills", response_model=SkillsOut,
             status_code=202, responses={404: {"description": "Сотрудник не найден"}})
def extract_skills(
    request: Request, employee_key: Annotated[str, EMPLOYEE_KEY],
    ctx: SessionContext = Depends(authenticated_csrf),
) -> SkillsOut:
    """Выделить навыки Основного CV, если ещё не выделялись (или прошлая попытка упала).
    Без замены файла уже извлечённые навыки не пересчитываются."""
    if not _cv(request).employee_exists(employee_key):
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    r = _service(request).schedule_skills(employee_key, ctx.login)
    return SkillsOut(employee_key=r.employee_key, outcome=r.outcome, task_id=r.task_id,
                     message=r.message)


@router.post("/resume/skills/backfill", response_model=SkillsBatchOut, status_code=202)
def backfill_skills(
    request: Request, ctx: SessionContext = Depends(authenticated_csrf),
) -> SkillsBatchOut:
    """Поставить выделение навыков всем, у кого основное CV есть, а навыков нет."""
    results = _service(request).backfill_skills(ctx.login)
    return SkillsBatchOut(
        items=[SkillsOut(employee_key=r.employee_key, outcome=r.outcome, task_id=r.task_id,
                         message=r.message) for r in results],
        scheduled=sum(r.outcome == "scheduled" for r in results))


# --- «Резюме»: фильтры и профиль (FR-032, FR-003) ---

@router.get("/resume/employees", response_model=ResumeList)
def list_resume(
    request: Request,
    q: str | None = Query(None, max_length=200,
                          description="Поиск по ФИО, ключу, имени файла и тексту CV"),
    status: str | None = Query(None, max_length=128, description="Статус сотрудника из Jira"),
    technology: Annotated[
        list[str] | None, Query(description="Технологии (повторяемый параметр)")] = None,
    match: str = Query("all", pattern="^(all|any)$",
                       description="all - есть все выбранные технологии; any - любая"),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    _: SessionContext = Depends(authenticated),
) -> ResumeList:
    """Таблица раздела «Резюме» с фильтрами без LLM. Технологии - из навыков Основного CV."""
    svc = _service(request)
    svc.collect_pending_skills()
    tags = sorted({t.strip().lower() for t in (technology or []) if t.strip()})
    rows, total = svc.list_resume(
        query=(q or "").strip() or None, status=status or None, tags=tags,
        match_all=match == "all", limit=limit, offset=offset)
    return ResumeList(total=total, items=[ResumeItem(
        employee_key=r.employee_key, full_name=r.full_name, status=r.status,
        assignable=r.assignable, description=r.extra_experience, vacation_plan=r.vacation_plan,
        note=r.note, note_author=r.note_author, note_updated_at=r.note_updated_at,
        main_cv_file_id=r.file_id, main_cv_file_name=r.file_name,
        extraction_status=r.extraction_status, skills=list(r.skills),
        skills_status=r.skills_status) for r in rows])


@router.get("/resume/technologies", response_model=list[TechnologyOut])
def list_technologies(
    request: Request, _: SessionContext = Depends(authenticated),
) -> list[TechnologyOut]:
    """Подсказки фильтра «Технология»: теги Основных CV с числом сотрудников."""
    return [TechnologyOut(tag=t, employees=n) for t, n in _service(request).technologies()]


@router.get("/resume/employees/{employee_key}/profile", response_model=ProfileOut,
            responses={404: {"description": "Сотрудник не найден"}})
def employee_profile(
    request: Request, employee_key: Annotated[str, EMPLOYEE_KEY],
    _: SessionContext = Depends(authenticated),
) -> ProfileOut:
    """Профиль из Основного CV (разделы формата BR-007) и карточки Jira. Внешняя LLM не
    вызывается (FR-003)."""
    svc = _service(request)
    card = svc.employee(employee_key)
    if card is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    cv_repo = _cv(request)
    files = [f for f in cv_repo.list_files(employee_key) if not f.is_archived]
    order = {"main_cv": 0, "extra_cv": 1, "client_template_cv": 2}
    links = [FileLink(id=f.id, kind=f.kind, file_name=f.file_name)
             for f in sorted(files, key=lambda f: order.get(f.kind, 9)) if f.kind in order]
    main = next((f for f in files if f.kind == "main_cv"), None)
    text = skills = state = None
    if main is not None:
        got = cv_repo.get_text(main.id)
        text = got[1] if got and main.extraction_status == "ok" else None
        skills, state = svc.skills_of(main.id)
    return ProfileOut(
        employee_key=card.employee_key, full_name=card.full_name, status=card.status,
        assignable=card.assignable, description=card.extra_experience,
        vacation_plan=card.vacation_plan, note=card.note, note_author=card.note_author,
        note_updated_at=card.note_updated_at,
        main_cv=FileLink(id=main.id, kind=main.kind, file_name=main.file_name) if main else None,
        files=links, skills=list(skills or ()), skills_status=state,
        sections=parse_sections(text), experience_years=guess_years(text),
        synced_at=card.synced_at)

