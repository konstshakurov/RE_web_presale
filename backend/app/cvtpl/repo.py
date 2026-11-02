"""SQL-источники модуля cvtpl: потребность (app_db), клиент (эпик) - справочник.

Отдельного SQL нет: файлы CV - уже в `` CvRepo`` (роль cv_rw), потребности - кэш Jira
в app_db (роль app_rw). Здесь только фасад для сервиса.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .service import NeedInfo

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

_EPIC_COLUMNS = "SELECT key, name FROM app.jira_epic WHERE key = %s"


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
        return row[1] if row else epic_key

    def names_of(self, keys: list[str]) -> dict[str, str | None]:
        if not keys:
            return {}
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT key, name FROM app.jira_epic WHERE key = ANY(%s)", (keys,)
            ).fetchall()
        found = {r[0]: r[1] for r in rows}
        return {k: found.get(k, k) for k in keys}
