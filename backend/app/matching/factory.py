"""Сборка модуля matching для api и worker."""
from __future__ import annotations

from psycopg_pool import ConnectionPool

from ..llm_gateway import LlmGateway
from .repo import MatchRepo, NeedSource
from .service import PURPOSES, MatchingService
from .settings import AppSettings


def build_matching(
    app_pool: ConnectionPool, cv_pool: ConnectionPool | None, gateway: LlmGateway,
) -> MatchingService | None:
    """None, если cv_db не настроена (CV_DB_URL): сотрудников и CV тогда нет."""
    if cv_pool is None:
        return None
    return MatchingService(
        MatchRepo(cv_pool), NeedSource(app_pool), gateway, settings=AppSettings(app_pool))


def task_handler(service: MatchingService):
    """Обработчик для ``LlmRunner(on_finished=...)``: после задачи шлюза забирает результат."""
    def handle(task) -> None:
        if task.purpose in PURPOSES:
            service.collect_task(task.id)
    return handle
