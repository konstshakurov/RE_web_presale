"""Сборка SyncService для api и worker из конфига приложения."""
from __future__ import annotations

import logging

from psycopg_pool import ConnectionPool

from ..app_repo import AppRepo, create_pool
from ..config import Settings
from ..secrets import SecretStore
from .repo import SyncRepo
from .service import SyncService
from .settings import SyncSettings, load_sync_settings

log = logging.getLogger(__name__)


def build_jira_adapter(settings: Settings):
    """JiraAdapter, если задан JIRA_BASE_URL и доступен jira_client; иначе None."""
    if not settings.jira_base_url:
        return None
    try:
        from ..integrations.jira import JiraAdapter

        return JiraAdapter()
    except Exception as exc:  # noqa: BLE001 - нет пакета/ключа: синхронизация «не включена»
        log.warning("Jira-адаптер не создан: %s", type(exc).__name__)
        return None


def open_cv_pool(settings: Settings) -> ConnectionPool | None:
    """Пул cv_db (роль cv_rw); None, если CV_DB_URL не задан - сотрудники недоступны."""
    if not settings.cv_db_url:
        return None
    pool = create_pool(settings.cv_db_url.reveal(), max_size=4)
    pool.open(wait=True)
    return pool


def build_sync_service(
    settings: Settings, app_pool: ConnectionPool, cv_pool: ConnectionPool | None, jira,
    sync_settings: SyncSettings | None = None,
) -> SyncService:
    store = SecretStore(AppRepo(app_pool), settings.enc_key_bytes(), settings.enc_key_id)
    return SyncService(
        SyncRepo(app_pool, cv_pool), jira, store, sync_settings or load_sync_settings()
    )
