"""Чтение карточки сотрудника для назначения (роль cv_rw, пул cv_db; NFR-007)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool


class EmployeeProfiles:
    def __init__(self, cv_pool: ConnectionPool) -> None:
        self._pool = cv_pool

    def get_profile(self, employee_key: str) -> tuple[bool, str | None]:
        with self._pool.connection() as conn:
            r = conn.execute(
                "SELECT jira_login FROM cv.employee WHERE jira_key = %s", (employee_key,)
            ).fetchone()
        return (True, r[0]) if r else (False, None)
