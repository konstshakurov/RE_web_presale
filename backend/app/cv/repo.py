"""SQL к cv.employee_file / cv.employee_file_content (роль cv_rw, отдельный пул, NFR-007).

Правила FR-039/FR-040 держит БД (cv.replace_main_cv, cv.restore_main_cv, триггеры V003);
здесь только вызовы и разбор отказов. Параметры - плейсхолдеры.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from psycopg import errors

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

OK, NOT_FOUND, PROTECTED, NOT_ARCHIVED = "ok", "not_found", "protected", "not_archived"

_META = (
    "f.id, f.employee_key, f.kind, f.client_epic_key, f.file_name, f.mime_type, f.size_bytes, "
    "f.is_archived, f.archived_at, f.extraction_status, f.uploaded_by, f.uploaded_at"
)


@dataclass(frozen=True)
class FileRow:
    id: int
    employee_key: str
    kind: str
    client_epic_key: str | None
    file_name: str
    mime_type: str
    size_bytes: int
    is_archived: bool
    archived_at: datetime | None
    extraction_status: str
    uploaded_by: str
    uploaded_at: datetime

    @property
    def is_current_main(self) -> bool:
        return self.kind == "main_cv" and not self.is_archived


def _row(r) -> FileRow:
    return FileRow(*r)


class CvRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def employee_exists(self, employee_key: str) -> bool:
        with self._pool.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM cv.employee WHERE jira_key = %s", (employee_key,)
            ).fetchone() is not None

    def list_employee_names(self) -> list[str]:
        """ФИО всех карточек: словарь обезличивания шлюза LLM (FR-023)."""
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT full_name FROM cv.employee").fetchall()
        return [r[0] for r in rows]

    def list_files(
        self, employee_key: str | None = None, *, include_archived: bool = False
    ) -> list[FileRow]:
        """Метаданные без содержимого и текста. Порядок: сотрудник, тип, свежие сверху."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_META} FROM cv.employee_file f "
                "WHERE (%s::text IS NULL OR f.employee_key = %s::text) "
                "AND (%s OR NOT f.is_archived) "
                "ORDER BY f.employee_key, f.is_archived, "
                "CASE f.kind WHEN 'main_cv' THEN 0 WHEN 'extra_cv' THEN 1 "
                "  WHEN 'client_template_cv' THEN 2 ELSE 3 END, "
                "COALESCE(f.archived_at, f.uploaded_at) DESC, f.id DESC",
                (employee_key, employee_key, include_archived),
            ).fetchall()
        return [_row(r) for r in rows]

    def get_file(self, file_id: int) -> FileRow | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                f"SELECT {_META} FROM cv.employee_file f WHERE f.id = %s", (file_id,)
            ).fetchone()
        return _row(r) if r else None

    def get_content(self, file_id: int) -> tuple[FileRow, bytes] | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                f"SELECT {_META}, c.content FROM cv.employee_file f "
                "JOIN cv.employee_file_content c ON c.file_id = f.id WHERE f.id = %s",
                (file_id,),
            ).fetchone()
        return (_row(r[:-1]), bytes(r[-1])) if r else None

    def get_text(self, file_id: int) -> tuple[FileRow, str | None] | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                f"SELECT {_META}, f.extracted_text FROM cv.employee_file f WHERE f.id = %s",
                (file_id,),
            ).fetchone()
        return (_row(r[:-1]), r[-1]) if r else None

    def current_main(self, employee_key: str) -> FileRow | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                f"SELECT {_META} FROM cv.employee_file f "
                "WHERE f.employee_key = %s AND f.kind = 'main_cv' AND NOT f.is_archived",
                (employee_key,),
            ).fetchone()
        return _row(r) if r else None

    def add_file(
        self, *, employee_key: str, kind: str, file_name: str, mime_type: str, content: bytes,
        uploaded_by: str, extracted_text: str | None, extraction_status: str,
    ) -> int:
        """Новый файл. Для main_cv - замена с архивом 3 версий (cv.replace_main_cv, FR-039)."""
        with self._pool.connection() as conn, conn.transaction():
            if kind == "main_cv":
                return conn.execute(
                    "SELECT cv.replace_main_cv(%s, %s, %s, %s, %s, %s, %s)",
                    (employee_key, file_name, mime_type, content, uploaded_by,
                     extracted_text, extraction_status),
                ).fetchone()[0]
            file_id = conn.execute(
                "INSERT INTO cv.employee_file (employee_key, kind, file_name, mime_type, "
                "size_bytes, sha256, extracted_text, extraction_status, uploaded_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (employee_key, kind, file_name, mime_type, len(content),
                 hashlib.sha256(content).digest(), extracted_text, extraction_status,
                 uploaded_by),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO cv.employee_file_content (file_id, content) VALUES (%s, %s)",
                (file_id, content),
            )
            return file_id

    def delete_file(self, file_id: int) -> str:
        """Актуальное основное CV не удаляется (FR-040): проверка здесь и триггер БД."""
        f = self.get_file(file_id)
        if f is None:
            return NOT_FOUND
        if f.is_current_main:
            return PROTECTED
        try:
            with self._pool.connection() as conn:
                n = conn.execute("DELETE FROM cv.employee_file WHERE id = %s", (file_id,)).rowcount
        except errors.RestrictViolation:
            return PROTECTED
        return OK if n else NOT_FOUND

    def restore_main(self, file_id: int) -> str:
        """Версия из архива становится актуальной, текущая уходит в архив (FR-039, п. 4)."""
        try:
            with self._pool.connection() as conn, conn.transaction():
                conn.execute("SELECT cv.restore_main_cv(%s)", (file_id,))
        except errors.NoDataFound:
            return NOT_FOUND if self.get_file(file_id) is None else NOT_ARCHIVED
        return OK
