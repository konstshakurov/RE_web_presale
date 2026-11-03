"""SQL-источники модуля cvtpl: потребность (app_db), клиент (эпик) - справочник.

Отдельного SQL нет: файлы CV - уже в `` CvRepo`` (роль cv_rw), потребности - кэш Jira
в app_db (роль app_rw). Здесь только фасад для сервиса.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .service import NeedInfo

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

_EPIC_COLUMNS = "SELECT name FROM app.jira_epic WHERE epic_key = %s"


class AppNeedSource:
    """Потребность из кэша Jira - app_db (как в NeedSource matching), плюс имя клиента."""

    def __init__(self, app_pool: ConnectionPool) -> None:
        self._pool = app_pool

    def find_need(self, key: str) -> NeedInfo | None:
        """``NeedInfo(title, epic_key)``; None, если потребности в кэше нет."""
        if not key:
            return None
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT title, epic_key FROM app.jira_need WHERE jira_key = %s", (key,)
            ).fetchone()
        if row is None:
            return None
        return NeedInfo(title=row[0] or key, epic_key=row[1])


class EpicNames:
    """Имена клиентов (эпиков Jira) по ключу: только для отображения в UI."""

    def __init__(self, app_pool: ConnectionPool) -> None:
        self._pool = app_pool

    def name_of(self, epic_key: str | None) -> str | None:
        if not epic_key:
            return None
        with self._pool.connection() as conn:
            row = conn.execute(_EPIC_COLUMNS, (epic_key,)).fetchone()
        return row[0] if row else epic_key

    def names_of(self, keys: list[str]) -> dict[str, str | None]:
        if not keys:
            return {}
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT epic_key, name FROM app.jira_epic WHERE epic_key = ANY(%s)", (keys,)
            ).fetchall()
        found = {r[0]: r[1] for r in rows}
        return {k: found.get(k, k) for k in keys}


class CvTplTemplateStore:
    """Хранилище шаблонов в app_db (V009): put/get/delete/purge (роль app_rw)."""

    def __init__(self, app_pool: ConnectionPool) -> None:
        self._pool = app_pool

    def put(self, task_id: str, owner_login: str, file_name: str, content: bytes) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO app.cvtpl_template (task_id, owner_login, file_name, content) "
                "VALUES (%s::uuid, %s, %s, %s) ON CONFLICT (task_id) DO UPDATE "
                "SET owner_login = EXCLUDED.owner_login, file_name = EXCLUDED.file_name, "
                "content = EXCLUDED.content, created_at = now()",
                (task_id, owner_login, file_name, content))

    def get(self, task_id: str, owner_login: str) -> bytes | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT content FROM app.cvtpl_template WHERE task_id = %s::uuid "
                "AND owner_login = %s", (task_id, owner_login)).fetchone()
        return bytes(row[0]) if row else None

    def delete(self, task_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM app.cvtpl_template WHERE task_id = %s::uuid", (task_id,))

    def purge_older_than(self, days: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM app.cvtpl_template "
                "WHERE created_at < now() - make_interval(days => %s)", (days,))


def build_cvtpl(gateway, cv_repo, app_pool, *, limit_mb: int) -> CvTplService:
    return CvTplService(
        gateway, cv_repo, AppNeedSource(app_pool), limit_mb=limit_mb,
        templates=CvTplTemplateStore(app_pool))
