"""Сборка модуля assign для api. None, если Jira не включена (запись недоступна)."""
from __future__ import annotations

from collections.abc import Callable

from psycopg_pool import ConnectionPool

from ..cv.repo import CvRepo
from ..sync.repo import SyncRepo
from .repo import EmployeeProfiles
from .service import AssignService


def build_assign(
    jira_adapter, token_of: Callable[[str], str | None], app_pool: ConnectionPool,
    cv_pool: ConnectionPool | None,
) -> AssignService | None:
    if jira_adapter is None:
        return None
    return AssignService(
        jira_adapter, token_of,
        EmployeeProfiles(cv_pool) if cv_pool is not None else None,
        CvRepo(cv_pool) if cv_pool is not None else None,
        SyncRepo(app_pool, cv_pool),
    )
