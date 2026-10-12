"""API файлов сотрудников и CV (FR-004, FR-022, FR-027, FR-037, FR-039, FR-040, NFR-004).

Загрузка - один файл за запрос, тело запроса = байты файла (не multipart), тип и имя - в query.
Права как у остальных защищённых эндпоинтов: сессия, CSRF на изменяющих. Автоудаления нет
(NFR-004): файл исчезает только по явному DELETE или вытеснением 4-й версии из архива (FR-039).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..auth.deps import SessionContext, authenticated, authenticated_csrf, require_admin
from . import extract
from .extract import ExtractError
from .formats import (
    CV_EXTENSIONS,
    CV_KINDS,
    EXTRACTABLE,
    KIND_TITLES,
    MATERIAL_EXTENSIONS,
    MIME,
    UPLOAD_KINDS,
    UploadKind,
    allowed_extensions,
    clean_file_name,
    extension,
    signature_ok,
)
from .limits import HARD_MAX_MB, MIN_MB, CvLimits
from .repo import NOT_ARCHIVED, NOT_FOUND, PROTECTED, CvRepo, FileRow

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/cv", tags=["cv"],
    responses={401: {"description": "Требуется вход"}, 403: {"description": "CSRF или права"}},
)

NO_STORE_MSG = "Хранилище CV не настроено (CV_DB_URL)"
FILE_NOT_FOUND = "Файл не найден"
EMPLOYEE_NOT_FOUND = "Сотрудник не найден"
EXTRACT_ERROR_GENERIC = "Не удалось извлечь текст из файла"


class FileOut(BaseModel):
    id: int
    employee_key: str
    kind: str = Field(description="main_cv | extra_cv | materials | client_template_cv")
    kind_title: str
    client_epic_key: str | None = Field(None, description="Только для client_template_cv")
    file_name: str
    mime_type: str
    size_bytes: int
    is_archived: bool = Field(description="true - прежняя версия основного CV (архив, FR-039)")
    archived_at: datetime | None = None
    uploaded_by: str
    uploaded_at: datetime
    extraction_status: str = Field(
        description="ok | error | none; none у «Материалов» - текст не извлекается")
    extraction_message: str | None = Field(
        None, description="Причина, если extraction_status=error")
    can_delete: bool = Field(description="false - актуальное основное CV: только заменяется")
    can_restore: bool = Field(description="true - архивная версия основного CV")


class EmployeeFiles(BaseModel):
    employee_key: str
    files: list[FileOut] = Field(description="Актуальные файлы: основное CV, доп. CV, материалы")
    archive: list[FileOut] = Field(description="Прежние версии основного CV, до 3, свежие сверху")


class FilesList(BaseModel):
    items: list[FileOut]


class UploadResult(BaseModel):
    file: FileOut
    replaced_file_id: int | None = Field(
        None, description="Для main_cv: прежнее актуальное CV, ушедшее в архив")


class FileText(BaseModel):
    file: FileOut
    text: str | None = Field(description="Извлечённый текст; null при extraction_status != ok")
    message: str | None = None


class MainCvText(BaseModel):
    employee_key: str
    file: FileOut | None = Field(description="null - у сотрудника нет основного CV")
    extraction_status: str = Field(description="ok | error | none (нет файла)")
    text: str | None = None
    message: str | None = None


class LimitsOut(BaseModel):
    max_file_mb: int
    max_file_bytes: int
    max_file_mb_ceiling: int = Field(description="Потолок 10 МБ задан в БД (ADR-009)")
    upload_kinds: dict[str, str] = Field(description="Тип файла -> название")
    cv_extensions: list[str]
    materials_extensions: list[str]
    text_extraction: list[str] = Field(description="Расширения, из которых извлекается текст")


class LimitsRequest(BaseModel):
    max_file_mb: int = Field(ge=MIN_MB, le=HARD_MAX_MB, description="Целое число МБ, 1..10")


def _repo(request: Request) -> CvRepo:
    repo: CvRepo | None = getattr(request.app.state, "cv", None)
    if repo is None:
        raise HTTPException(status_code=503, detail=NO_STORE_MSG)
    return repo


def _limits(request: Request) -> CvLimits:
    return request.app.state.cv_limits


def extraction_message(f: FileRow) -> str | None:
    if f.extraction_status != "error":
        return None
    ext = extension(f.file_name)
    if ext not in EXTRACTABLE:
        return f"Извлечение текста для формата .{ext or '?'} не поддерживается"
    return EXTRACT_ERROR_GENERIC + " (файл повреждён, защищён паролем или без текста)"


def file_out(f: FileRow) -> FileOut:
    return FileOut(
        id=f.id, employee_key=f.employee_key, kind=f.kind, kind_title=KIND_TITLES[f.kind],
        client_epic_key=f.client_epic_key, file_name=f.file_name, mime_type=f.mime_type,
        size_bytes=f.size_bytes, is_archived=f.is_archived, archived_at=f.archived_at,
        uploaded_by=f.uploaded_by, uploaded_at=f.uploaded_at,
        extraction_status=f.extraction_status, extraction_message=extraction_message(f),
        can_delete=not f.is_current_main, can_restore=f.kind == "main_cv" and f.is_archived,
    )


def _file_or_404(repo: CvRepo, file_id: int) -> FileRow:
    f = repo.get_file(file_id)
    if f is None:
        raise HTTPException(status_code=404, detail=FILE_NOT_FOUND)
    return f


def _content_disposition(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


@router.get("/limits", response_model=LimitsOut)
def get_limits(request: Request, _: SessionContext = Depends(authenticated)) -> LimitsOut:
    """Лимит и форматы для окна загрузки (FR-022 п. 3)."""
    mb = _limits(request).max_mb()
    return LimitsOut(
        max_file_mb=mb, max_file_bytes=mb * 1024 * 1024, max_file_mb_ceiling=HARD_MAX_MB,
        upload_kinds={k: KIND_TITLES[k] for k in UPLOAD_KINDS},
        cv_extensions=sorted(CV_EXTENSIONS), materials_extensions=sorted(MATERIAL_EXTENSIONS),
        text_extraction=sorted(EXTRACTABLE),
    )


@router.put("/limits", response_model=LimitsOut, responses={422: {}})
def set_limits(
    body: LimitsRequest, request: Request, ctx: SessionContext = Depends(require_admin),
) -> LimitsOut:
    """Лимит файла (только admin). Выше 10 МБ нельзя: потолок в схеме cv_db (ADR-009)."""
    _limits(request).set_max_mb(body.max_file_mb)
    log.info("cv: %s изменил лимит файла: %s МБ", ctx.login, body.max_file_mb)
    return get_limits(request, ctx)


@router.get("/files", response_model=FilesList)
def list_all_files(
    request: Request, _: SessionContext = Depends(authenticated),
) -> FilesList:
    """Актуальные файлы всех сотрудников одним запросом: столбец «Файлы» раздела «Резюме»."""
    return FilesList(items=[file_out(f) for f in _repo(request).list_files()])


@router.get("/employees/{employee_key}/files", response_model=EmployeeFiles,
            responses={404: {"description": EMPLOYEE_NOT_FOUND}})
def employee_files(
    employee_key: str, request: Request, _: SessionContext = Depends(authenticated),
) -> EmployeeFiles:
    repo = _repo(request)
    if not repo.employee_exists(employee_key):
        raise HTTPException(status_code=404, detail=EMPLOYEE_NOT_FOUND)
    rows = repo.list_files(employee_key, include_archived=True)
    return EmployeeFiles(
        employee_key=employee_key,
        files=[file_out(f) for f in rows if not f.is_archived],
        archive=[file_out(f) for f in rows if f.is_archived],
    )


@router.post(
    "/employees/{employee_key}/files", response_model=UploadResult, status_code=201,
    responses={
        404: {"description": EMPLOYEE_NOT_FOUND},
        413: {"description": "Файл больше лимита"},
        415: {"description": "Недопустимый формат"},
        422: {"description": "Нет типа, имени или тела"},
    },
)
async def upload_file(
    employee_key: str, request: Request,
    kind: Annotated[UploadKind, Query(description="Тип файла - обязателен (FR-022 п. 1)")],
    file_name: Annotated[str, Query(min_length=1, max_length=1024, description="Имя файла")],
    ctx: SessionContext = Depends(authenticated_csrf),
) -> UploadResult:
    """Загрузить ОДИН файл: тело запроса - содержимое файла (Content-Type любой).

    Недопустимый формат и файл больше лимита отклоняются, в карточку не попадают (FR-022 п. 3).
    Основное CV заменяет прежнее, прежнее уходит в архив (FR-039); текст извлекается при
    загрузке (DOC/DOCX/TXT, FR-027): сбой извлечения не отменяет загрузку, статус ``error``.
    """
    repo = _repo(request)
    limit = _limits(request).max_bytes()
    name = clean_file_name(file_name)
    ext = extension(name)
    if not name or ext not in allowed_extensions(kind):
        allowed = ", ".join("." + e for e in sorted(allowed_extensions(kind)))
        raise HTTPException(
            status_code=415, detail=f"Недопустимый формат файла. Допустимо: {allowed}")
    if not await run_in_threadpool(repo.employee_exists, employee_key):
        raise HTTPException(status_code=404, detail=EMPLOYEE_NOT_FOUND)

    too_big = HTTPException(
        status_code=413, detail=f"Файл больше лимита {limit // (1024 * 1024)} МБ")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise too_big
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():  # не читаем сверх лимита
        size += len(chunk)
        if size > limit:
            raise too_big
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=422, detail="Файл пустой")
    if not signature_ok(ext, content[:1024]):
        raise HTTPException(
            status_code=415, detail="Содержимое файла не соответствует его расширению")

    result = await run_in_threadpool(
        _store, repo, employee_key, kind, name, ext, content, ctx.login)
    log.info("cv: %s загрузил файл id=%s сотрудника %s тип=%s", ctx.login, result.file.id,
             employee_key, kind)
    return result


def _store(
    repo: CvRepo, employee_key: str, kind: str, name: str, ext: str, content: bytes, login: str,
) -> UploadResult:
    text: str | None = None
    status = extract.NONE
    message: str | None = None
    if kind in CV_KINDS:
        try:
            text, status = extract.extract_text(name, content), extract.OK
        except ExtractError as exc:
            status, message = extract.ERROR, str(exc)
    previous = repo.current_main(employee_key) if kind == "main_cv" else None
    file_id = repo.add_file(
        employee_key=employee_key, kind=kind, file_name=name, mime_type=MIME[ext],
        content=content, uploaded_by=login, extracted_text=text, extraction_status=status,
    )
    saved = repo.get_file(file_id)
    out = file_out(saved)
    if message:  # точная причина - только в ответе на загрузку
        out.extraction_message = message
    return UploadResult(file=out, replaced_file_id=previous.id if previous else None)


@router.get("/files/{file_id}/download", responses={404: {}},
            response_class=Response)
def download_file(
    file_id: int, request: Request, _: SessionContext = Depends(authenticated),
) -> Response:
    """Исходный файл без изменений (FR-040 п. 1), в том числе версия из архива."""
    got = _repo(request).get_content(file_id)
    if got is None:
        raise HTTPException(status_code=404, detail=FILE_NOT_FOUND)
    meta, content = got
    return Response(
        content=content, media_type=meta.mime_type,
        headers={
            "Content-Disposition": _content_disposition(meta.file_name),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/files/{file_id}/text", response_model=FileText, responses={404: {}})
def file_text(
    file_id: int, request: Request, _: SessionContext = Depends(authenticated),
) -> FileText:
    """Извлечённый текст для окна просмотра (FR-037)."""
    got = _repo(request).get_text(file_id)
    if got is None:
        raise HTTPException(status_code=404, detail=FILE_NOT_FOUND)
    meta, text = got
    return FileText(file=file_out(meta), text=text if meta.extraction_status == "ok" else None,
                    message=extraction_message(meta))


@router.get("/employees/{employee_key}/main-cv", response_model=MainCvText,
            responses={404: {"description": EMPLOYEE_NOT_FOUND}})
def main_cv_text(
    employee_key: str, request: Request, _: SessionContext = Depends(authenticated),
) -> MainCvText:
    """Текст актуального основного CV (кнопка «Резюме», FR-037; вход для FR-027/FR-036).
    Нет файла - 200 со статусом ``none``."""
    repo = _repo(request)
    if not repo.employee_exists(employee_key):
        raise HTTPException(status_code=404, detail=EMPLOYEE_NOT_FOUND)
    cur = repo.current_main(employee_key)
    if cur is None:
        return MainCvText(employee_key=employee_key, file=None, extraction_status="none",
                          message="Основное CV не загружено")
    got = repo.get_text(cur.id)
    if got is None:  # удалено между двумя запросами
        return MainCvText(employee_key=employee_key, file=None, extraction_status="none",
                          message="Основное CV не загружено")
    meta, text = got
    return MainCvText(
        employee_key=employee_key, file=file_out(meta), extraction_status=meta.extraction_status,
        text=text if meta.extraction_status == "ok" else None, message=extraction_message(meta),
    )


@router.delete("/files/{file_id}", status_code=204, response_class=Response,
               responses={404: {}, 409: {"description": "Актуальное основное CV не удаляется"}})
def delete_file(
    file_id: int, request: Request, ctx: SessionContext = Depends(authenticated_csrf),
) -> Response:
    """Удалить файл (FR-040). Актуальное основное CV - 409: оно только заменяется (FR-039)."""
    result = _repo(request).delete_file(file_id)
    if result == NOT_FOUND:
        raise HTTPException(status_code=404, detail=FILE_NOT_FOUND)
    if result == PROTECTED:
        raise HTTPException(
            status_code=409,
            detail="Актуальное основное CV не удаляется, его можно только заменить новым",
        )
    log.info("cv: %s удалил файл id=%s", ctx.login, file_id)
    return Response(status_code=204)


@router.post("/files/{file_id}/restore", response_model=EmployeeFiles,
             responses={404: {}, 409: {"description": "Не архивная версия основного CV"}})
def restore_file(
    file_id: int, request: Request, ctx: SessionContext = Depends(authenticated_csrf),
) -> EmployeeFiles:
    """Восстановить версию из архива: она становится актуальным основным CV, текущее уходит
    в архив (FR-039 п. 4)."""
    repo = _repo(request)
    f = _file_or_404(repo, file_id)
    result = repo.restore_main(file_id)
    if result == NOT_FOUND:
        raise HTTPException(status_code=404, detail=FILE_NOT_FOUND)
    if result == NOT_ARCHIVED:
        raise HTTPException(
            status_code=409, detail="Восстановить можно только архивную версию основного CV")
    log.info("cv: %s восстановил версию основного CV id=%s", ctx.login, file_id)
    return employee_files(f.employee_key, request, ctx)
