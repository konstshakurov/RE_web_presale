"""Сборка модуля client_rules для api и worker."""
from __future__ import annotations

from ..llm_gateway import LlmGateway
from ..llm_gateway.repo import LlmRepo
from ..sync.repo import SyncRepo
from .service import PURPOSE, ClientRulesService


def build_client_rules(
    app_pool,  # psycopg_pool.ConnectionPool (app_db)
    gateway: LlmGateway,
    *,
    sync_repo: SyncRepo | None = None,
    jira_base_url: str = "",
) -> ClientRulesService:
    """sync_repo уже собран у SyncService (api/worker) - используем его; иначе свой."""
    return ClientRulesService(
        sync_repo or SyncRepo(app_pool), LlmRepo(app_pool), gateway,
        jira_base_url=jira_base_url)


def task_handler(service: ClientRulesService):
    """Обработчик для ``LlmRunner(on_finished=...)``: после задачи шлюза забирает тезисы."""
    def handle(task) -> None:
        if task.purpose == PURPOSE:
            service.collect_task(task.id)
    return handle
