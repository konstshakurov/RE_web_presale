"""Логика CV по шаблону клиента (FR-017; KON-80/Pre.Dev-14): без HTTP и без сети.

Задача LLM ставится через шлюз (``purpose="cv_template"``, обезличивание внешнего
провайдера - в шлюзе). Повтор по паре «сотрудник + клиент» (BR-007) не блокирует:
в контексте диалога отдаётся последний сохранённый результат такого типа. Сохранение -
файл ``kind="client_template_cv"`` в карточке сотрудника с ``client_epic_key``
клиента потребности (выбирается в окне «Назначить» вместо Основного CV, FR-017).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..cv.extract import ExtractError, extract_text
from ..cv.repo import CvRepo, FileRow
from .docx_editor import apply_blocks_to_template
from .docxgen import build_docx
from .prompts import CV_TEMPLATE_SYSTEM, blocks_to_text, build_prompt, parse_result

PURPOSE = "cv_template"
#: Формат шаблона клиента (как у CV, FR-022): только DOC/DOCX
TEMPLATE_EXTENSIONS = frozenset({"doc", "docx"})
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class CvTplError(Exception):
    """Отказ операции (код + сообщение безопасно показывать пользователю)."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class NeedInfo:
    title: str
    epic_key: str | None


class Gateway(Protocol):
    """Часть ``LlmGateway``, которую использует модуль."""

    def submit(self, owner_login: str, purpose: str, prompt: str, *, system: str | None = None,
               provider_id: int | None = None, ref: str | None = None, terms=None): ...
    def get(self, owner_login: str, task_id: str): ...


class TemplateStore(Protocol):
    """Хранилище файла-шаблона между шагами generate -> save (миграция app_db V009)."""

    def put(self, task_id: str, owner_login: str, file_name: str, content: bytes) -> None: ...
    def get(self, task_id: str, owner_login: str) -> bytes | None: ...
    def delete(self, task_id: str) -> None: ...
    def purge_older_than(self, days: int) -> None: ...


class NeedSource(Protocol):
    def find_need(self, key: str) -> NeedInfo | None: ...


def _file_meta(f: FileRow) -> dict:
    return {
        "id": f.id, "kind": f.kind, "file_name": f.file_name,
        "client_epic_key": f.client_epic_key, "is_archived": f.is_archived,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
        "extraction_status": f.extraction_status,
    }


class CvTplService:
    def __init__(
        self, gateway: Gateway, cv: CvRepo, needs: NeedSource, *, limit_mb: int,
        templates: TemplateStore | None = None,
    ) -> None:
        self._gw = gateway
        self._cv = cv
        self._needs = needs
        self._limit = max(1, limit_mb)
        self._templates = templates

    # --- контекст диалога (шаг 1): Основное CV и сохранённые результаты (повтор, BR-007) ---

    def context(self, need_key: str, employee_key: str) -> dict:
        need = self._need(need_key)
        self._employee(employee_key)
        main = self._cv.current_main(employee_key)
        saved = [
            f for f in self._cv.list_files(employee_key)
            if f.kind == "client_template_cv" and not f.is_archived
            and (need.epic_key is None or f.client_epic_key == need.epic_key)
        ]
        repeat: dict | None = None
        if saved:
            r = saved[0]
            repeat = {
                "file_id": r.id, "file_name": r.file_name,
                "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            }
        return {
            "need_key": need_key, "need_title": need.title, "client_epic_key": need.epic_key,
            "main_cv": _file_meta(main) if main else None,
            "repeat": repeat,
            "saved": [_file_meta(f) for f in saved[:10]],
        }

    # --- старт генерации (шаг 1): шаблон загружается в диалоге (MVP; выбор вложения
    # потребности/эпика - вне MVP, design/KON-80/decision.md) ---

    def start(
        self, owner_login: str, need_key: str, employee_key: str, *,
        template_name: str, template_content: bytes,
        main_cv_file_id: int | None, provider_id: int | None,
    ) -> Any:
        if not template_content:
            raise CvTplError("empty_file", "Файл шаблона пустой", status=415)
        if len(template_content) > self._limit * 1024 * 1024:
            raise CvTplError("template_too_big", f"Файл шаблона больше {self._limit} МБ")
        ext = (template_name.rpartition(".")[2] or "").lower()
        if ext not in TEMPLATE_EXTENSIONS:
            raise CvTplError("bad_template_format", "Шаблон: только .doc или .docx (FR-022)")
        need = self._need(need_key)
        self._employee(employee_key)
        if need.epic_key is None:
            raise CvTplError("need_no_client", "У потребности не указан клиент (эпик)")
        main = self._main_file(employee_key, main_cv_file_id)
        template_text = self._text("шаблон клиента", template_name, template_content)
        _, cv_bytes = self._cv.get_content(main.id) or (None, b"")
        cv_text = self._text("основного CV", main.file_name, cv_bytes or b"")
        if self._templates is not None:
            # ленивая чистка: шаблоны старше 7 дней (задачи уже завершены/потеряны)
            self._templates.purge_older_than(7)
        task = self._gw.submit(
            owner_login, PURPOSE,
            build_prompt(
                template_text=template_text, cv_text=cv_text,
                need_title=need.title, client_label=need.epic_key),
            system=CV_TEMPLATE_SYSTEM, provider_id=provider_id,
            ref=f"{need_key}:{employee_key}",
            terms=None,
        )
        if self._templates is not None:
            # файл-шаблон нужен на шаге save: результат встраивается в его разметку
            self._templates.put(task.id, owner_login, template_name, template_content)
        return task

    # --- сохранение результата (шаг 3): файл в карточке сотрудника ---

    def save(self, owner_login: str, task_id: str, need_key: str, employee_key: str) -> dict:
        need = self._need(need_key)
        self._employee(employee_key)
        if need.epic_key is None:
            raise CvTplError("need_no_client", "У потребности не указан клиент (эпик)")
        result = self._result(owner_login, task_id)
        name = self._file_name(employee_key, need)
        template_bytes = (self._templates.get(task_id, owner_login)
                          if self._templates is not None else None)
        cv_text = blocks_to_text(result.get("cv_blocks"), result.get("cv") or "")
        if template_bytes:
            # документ = шаблон с вставленным контентом: стили, шрифты, цвета,
            # заголовки, таблицы и колонтитулы остаются родными (FR-017)
            content = apply_blocks_to_template(template_bytes, result.get("cv_blocks") or [])
        else:
            content = build_docx(cv_text)
        file_id = self._cv.add_file(
            employee_key=employee_key, kind="client_template_cv", file_name=name,
            mime_type=DOCX_MIME, content=content, uploaded_by=owner_login,
            extracted_text=cv_text, extraction_status="ok",
            client_epic_key=need.epic_key,
        )
        if self._templates is not None:
            self._templates.delete(task_id)  # шаблон одноразовый: использован при сохранении
        return {"file_id": file_id, "file_name": name, "client_epic_key": need.epic_key}

    # --- письмо: чтение из результата; публикацию выполняет router (Jira-адаптер сессии) ---

    def letter_of(self, owner_login: str, task_id: str) -> str:
        return self._result(owner_login, task_id)["letter"]

    # --- внутреннее ---

    def _result(self, owner_login: str, task_id: str) -> dict:
        task = self._gw.get(owner_login, task_id)
        if getattr(task, "purpose", "") != PURPOSE:
            raise CvTplError("task_not_found", "Задача не является CV по шаблону", status=404)
        if getattr(task, "status", "") != "done" or not getattr(task, "result", None):
            raise CvTplError(
                "task_not_done", "Задача не завершена (или завершена с ошибкой)", status=409)
        try:
            out = parse_result(task.result)
        except ValueError as exc:
            raise CvTplError("bad_result", f"Ответ LLM не разобрать: {exc}") from None
        return out

    def _need(self, need_key: str) -> NeedInfo:
        need = self._needs.find_need(need_key)
        if need is None:
            raise CvTplError("need_not_found", "Потребность не найдена", status=404)
        return need

    def _employee(self, employee_key: str) -> None:
        if not self._cv.employee_exists(employee_key):
            raise CvTplError("employee_not_found", "Сотрудник не найден", status=404)

    def _main_file(self, employee_key: str, main_cv_file_id: int | None) -> FileRow:
        if main_cv_file_id is not None:
            f = self._cv.get_file(main_cv_file_id)
        else:
            f = self._cv.current_main(employee_key)
        if (f is None or f.employee_key != employee_key or f.kind != "main_cv"
                or f.is_archived):
            raise CvTplError(
                "file_not_found", "Основное CV сотрудника не найдено (или не актуально)",
                status=404)
        return f

    def _text(self, what: str, file_name: str, content: bytes) -> str:
        if not content:
            raise CvTplError("empty_file", f"Файл {what}: пустое содержимое", status=415)
        try:
            return extract_text(file_name, content)
        except ExtractError as exc:
            raise CvTplError("extract_failed", f"Файл {what}: {exc}", status=415) from None

    def _file_name(self, employee_key: str, need: NeedInfo) -> str:
        """«CV <основа имени основного CV> <клиент>.docx» - опознаваемо и уникально в списке."""
        main = self._cv.current_main(employee_key)
        stem = (main.file_name.rpartition(".")[0] if main else employee_key).strip()
        client = (need.epic_key or "клиент").split(" ")[0]
        return f"CV {stem[:110]} {client}.docx"


__all__ = ["CvTplError", "CvTplService", "PURPOSE"]
