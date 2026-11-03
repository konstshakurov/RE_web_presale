"""API модуля cvtpl (KON-80/Pre.Dev-14): контекст диалога, генерация через шлюз LLM,
сохранение результата файлом, публикация письма комментарием Jira.

Задачу выполняет worker (ADR-007); статус читается владельцем задачи через общий
``GET /llm/tasks/{id}``. Публикация письма: комментарий потребности, ``confirmed=true``
обязателен (критерий Dev-14); отказ Jira - текст как есть (FR-029), истёкшая сессия -
409 ``needs_reauthorization``. Секреты и тексты CV в логи не попадают.
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import (APIRouter, Depends, HTTPException, Query, Request)
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..auth.deps import SessionContext, authenticated, authenticated_csrf
from ..assign.service import AssignError
from ..cv.repo import CvRepo
from ..jira_session.service import require_jira_ok
from .repo import EpicNames
from .service import CvTplError, CvTplService

router = APIRouter(
    tags=["cvtpl"],
    responses={401: {"description": "Требуется вход"}, 403: {"description": "CSRF"}},
)

_JIRA_HTTP = {
    "needs_reauthorization": (409, "needs_reauthorization"),
    "no_permission": (403, "no_permission"),
    "jira_unavailable": (503, "unavailable"),
    "not_enabled": (503, "not_enabled"),
    "auth_failed": (409, "needs_reauthorization"),
    "jira_error": (502, "blocked"),
}
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")
_STATE = {
    "need_not_found": "not_found", "employee_not_found": "not_found",
    "file_not_found": "not_found", "task_not_found": "not_found",
    "task_not_done": "conflict", "task_wrong_purpose": "not_found",
    "template_too_big": "too_big", "bad_template_format": "invalid",
    "extract_failed": "invalid_file", "empty_file": "invalid_file",
    "bad_result": "bad_result", "need_no_client": "conflict",
    "not_confirmed": "invalid",
}


def _service(request: Request) -> CvTplService:
    svc = getattr(request.app.state, "cvtpl", None)
    if svc is None:
        raise HTTPException(status_code=503, detail={
            "state": "not_enabled", "code": "not_enabled",
            "message": "Функция CV по шаблону недоступна"})
    return svc


def _cv(request: Request) -> CvRepo:
    repo = getattr(request.app.state, "cv", None)
    if repo is None:
        raise HTTPException(status_code=503, detail={
            "state": "not_enabled", "code": "not_enabled",
            "message": "cv_db не настроена: файлы CV недоступны"})
    return repo


def _need_key_check(need_key: str) -> None:
    if not _KEY_RE.match(need_key):
        raise HTTPException(status_code=422, detail={
            "state": "invalid", "code": "need_key_format",
            "message": "Неверный формат ключа потребности"})


def _employee_exists(request: Request, employee_key: str) -> None:
    if not _cv(request).employee_exists(employee_key):
        raise HTTPException(status_code=404, detail={
            "state": "not_found", "code": "employee_not_found",
            "message": "Сотрудник не найден"})


def _http_error(err: CvTplError) -> HTTPException:
    status = err.status if err.status is not None else 422
    return HTTPException(
        status_code=status,
        detail={"state": _STATE.get(err.code, "invalid"),
                "code": err.code, "message": err.message})


NEED_KEY_Q = Query(min_length=3, max_length=64, description="Ключ потребности Jira")
EMPLOYEE_KEY_Q = Query(min_length=3, max_length=64, description="Ключ сотрудника Jira")


@router.get("/cvtpl/context")
def context(
    need_key: Annotated[str, NEED_KEY_Q],
    employee_key: Annotated[str, EMPLOYEE_KEY_Q],
    request: Request, _ctx: SessionContext = Depends(authenticated),
) -> dict:
    """Шаг 1 диалога: потребность/клиент, Основное CV и сохранённые результаты
    по паре «сотрудник + клиент» (баннер повтора, BR-007)."""
    _need_key_check(need_key)
    _employee_exists(request, employee_key)
    try:
        data = _service(request).context(need_key, employee_key)
    except CvTplError as err:
        raise _http_error(err) from None
    data["client_name"] = EpicNames(request.app.state.pool).name_of(data.get("client_epic_key"))
    return data


# Задача шлюза LLM: загруженный шаблон + Основное CV (FR-017). Файл шаблона идёт
# байтами в теле запроса (как загрузка CV, KON-36) - multipart не требуется.
_TEMPLATE_Q = Query(min_length=3, max_length=255, description="Имя файла шаблона (для формата и имени результата)")


@router.post("/cvtpl/generate", status_code=201)
async def generate(
    request: Request,
    need_key: Annotated[str, Query(min_length=3, max_length=64)],
    employee_key: Annotated[str, Query(min_length=3, max_length=64)],
    template_name: Annotated[str, _TEMPLATE_Q],
    main_cv_file_id: Annotated[int | None, Query()] = None,
    provider_id: Annotated[int | None, Query()] = None,
    ctx: SessionContext = Depends(authenticated_csrf),
) -> dict:
    """Поставить задачу шлюза LLM: загруженный шаблон + Основное CV (FR-017).
    Файл шаблона - октет-стрим в теле (Content-Type: application/octet-stream).
    Внешний провайдер: ``requires_confirmation`` - подтвердить отправку через
    ``POST /llm/tasks/{id}/confirm`` (FR-023; текст уходит обезличенным)."""
    _need_key_check(need_key)
    _employee_exists(request, employee_key)
    svc = _service(request)
    content = await _read_template_bytes(request)
    try:
        task = await run_in_threadpool(
            svc.start, ctx.login, need_key, employee_key,
            template_name=template_name.strip(), template_content=content,
            main_cv_file_id=main_cv_file_id, provider_id=provider_id)
    except CvTplError as err:
        raise _http_error(err) from None
    return {
        "task_id": task.id, "status": task.status,
        "requires_confirmation": task.requires_confirmation,
    }


class TaskRef(BaseModel):
    need_key: str = Field(min_length=3, max_length=64)
    employee_key: str = Field(min_length=3, max_length=64)


class LetterRef(TaskRef):
    letter: str = Field(min_length=1, max_length=32000)
    confirmed: bool = Field(
        description="Явный апрув публикации в Jira; без него публикация запрещена (Dev-14)")


@router.post("/cvtpl/task/{task_id}/save")
def save_result(
    task_id: str, body: TaskRef, request: Request,
    ctx: SessionContext = Depends(authenticated_csrf),
) -> dict:
    """Сохранить результат задачи файлом ``kind="client_template_cv"``, привязанным к
    клиенту потребности (FR-017); файл сразу виден в «Назначить» и в «Файлы»."""
    _need_key_check(body.need_key)
    try:
        saved = _service(request).save(ctx.login, task_id, body.need_key, body.employee_key)
    except CvTplError as err:
        raise _http_error(err) from None
    return saved


def _jira(request: Request):
    jira = getattr(request.app.state, "jira", None)
    if jira is None:
        raise HTTPException(status_code=503, detail={
            "state": "not_enabled", "code": "not_enabled",
            "message": "Интеграция Jira не включена (KON-108)"})
    return jira


def _jira_error(err: AssignError) -> HTTPException:
    status, state = _JIRA_HTTP.get(err.code, (502, "blocked"))
    return HTTPException(status_code=status, detail={
        "state": state, "code": err.code, "message": err.message,
        "step": err.step, "completed": err.completed})


@router.post("/cvtpl/task/{task_id}/publish-letter")
async def publish_letter(
    task_id: str, body: LetterRef, request: Request,
    ctx: SessionContext = Depends(require_jira_ok),
) -> dict:
    """Сопроводительное письмо публикуется комментарием потребности только после
    явного апрува (``confirmed=true``); повторное нажатие блокирует клиент."""
    _need_key_check(body.need_key)
    if not body.confirmed:
        raise HTTPException(status_code=422, detail={
            "state": "invalid", "code": "not_confirmed",
            "message": "Публикация письма возможна только после явного подтверждения"})
    try:
        letter = await run_in_threadpool(
            _service(request).letter_of, ctx.login, task_id)
    except CvTplError as err:
        raise _http_error(err) from None
    jira = _jira(request)
    sealed = request.app.state.jira_session.get_user_jira_token(ctx.login)
    try:
        await run_in_threadpool(jira.add_comment, sealed, body.need_key, body.letter.strip() or letter)
    except AssignError as err:
        raise _jira_error(err) from None
    except Exception as exc:  # noqa: BLE001 - транспорт/таймаут Jira: 503 как в assign
        raise HTTPException(status_code=503, detail={
            "state": "unavailable", "code": "jira_unavailable",
            "message": f"Jira недоступна: {type(exc).__name__}"}) from None
    return {"published": True, "need_key": body.need_key}


async def _read_template_bytes(request: Request) -> bytes:
    """Тело запроса = байты файла шаблона с потолком лимита (FR-022/ADR-009: app_setting.max_file_mb)."""
    mb = request.app.state.cv_limits.max_mb()
    too_big = HTTPException(status_code=413, detail={
        "state": "too_big", "code": "template_too_big",
        "message": f"Файл шаблона больше {mb} МБ"})
    cap = mb * 1024 * 1024
    declared = request.headers.get("content-length")
    if not declared:
        raise HTTPException(status_code=422, detail={
            "state": "invalid", "code": "empty_file",
            "message": "Файл шаблона не передан (требуется octet-stream тело)"})
    if int(declared) > cap:
        raise too_big
    body = await request.body()
    if len(body) > cap:
        raise too_big
    if not body:
        raise HTTPException(status_code=422, detail={
            "state": "invalid", "code": "empty_file", "message": "Файл шаблона пустой"})
    return body
