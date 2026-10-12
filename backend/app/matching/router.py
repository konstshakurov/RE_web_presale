"""API модуля matching (KON-38): сопоставление потребности с кандидатами (FR-005, FR-036),
профиль сотрудника (FR-003), навыки Основного CV (FR-027), фильтры «Резюме» (FR-032).

Сопоставление асинхронное: ``POST /needs/{key}/match`` ставит задачи в шлюз LLM и сразу
возвращает состояние; результат опрашивается ``GET`` и берётся из кэша. Внешний провайдер:
в состоянии ``awaiting_confirmation`` задачи подтверждаются через ``POST /llm/tasks/{id}/confirm``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from ..auth.deps import SessionContext, authenticated, authenticated_csrf
from ..cv.repo import CvRepo
from ..llm_gateway import AnonymizationError, GatewayError
from .profile import guess_years, parse_sections
from .service import MatchingError, MatchingService, MatchState

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
    "prompt_too_long": 413,
}
_MATCHING_STATUS = {"need_not_found": 404, "employee_not_found": 404}


# --- модели ---

class MatchRequest(BaseModel):
    provider_id: int | None = Field(None, description="Провайдер LLM; пусто - по умолчанию")
    force: bool = Field(
        False, description="Пересчитать, даже если результат в кэше («Пересчитать»)")


class TaskOut(BaseModel):
    task_id: str = Field(description="Задача шлюза LLM: GET /llm/tasks/{id}, confirm, retry")
    status: str = Field(description="Статус задачи шлюза либо итог пакета: done | failed")
    requires_confirmation: bool = Field(
        description="true - внешний провайдер, нужен предпросмотр и confirm владельцем")
    batch_status: str = Field(description="pending | done | failed")
    candidates: int


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
        tasks=[TaskOut(task_id=t.task_id, status=t.status,
                       requires_confirmation=t.requires_confirmation,
                       batch_status=t.batch_status, candidates=t.candidates) for t in s.tasks],
        batches_total=s.batches_total, batches_done=s.batches_done,
        batches_failed=s.batches_failed, errors=s.errors,
    )


def _raise(exc: Exception) -> None:
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

