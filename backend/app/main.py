"""api: /health, старт (конфиг, пул app_db, логи, seed админов). Остальное добавляют Dev."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .app_repo import AppRepo, create_pool
from .auth import Ldap3Adapter
from .auth import router as auth_router
from .auth.admin import router as admin_router
from .bootstrap import seed_admins
from .config import load_settings
from .jira_session import DisabledJiraSession, JiraClientSessionAdapter, JiraSessionService
from .logging_setup import configure_logging
from .secrets import SecretStore
from .status_groups.repo import StatusGroupRepo
from .status_groups.router import router as status_groups_router
from .sync.factory import build_sync_service, open_cv_pool
from .sync.router import router as sync_router

log = logging.getLogger(__name__)


def _build_jira_adapter(settings):
    """JiraAdapter, если задан JIRA_BASE_URL и доступен jira_client; иначе None (вход в Jira
    «не включён», вход в приложение работает)."""
    if not settings.jira_base_url:
        return None
    try:
        from .integrations.jira import JiraAdapter

        return JiraAdapter()
    except Exception as exc:  # noqa: BLE001 - нет пакета/ключа: не роняем приложение
        log.warning("Jira-адаптер не создан: %s", type(exc).__name__)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ConfigError при пустом APP_DB_URL / APP_ENC_KEY останавливает запуск
    settings = load_settings()
    configure_logging(settings.secret_values())
    pool = create_pool(settings.app_db_url.reveal())
    pool.open(wait=True)
    repo = AppRepo(pool)
    app.state.settings = settings
    app.state.pool = pool
    app.state.repo = repo
    app.state.status_groups = StatusGroupRepo(pool)
    # без LDAP_URL приложение стартует, но /auth/login отвечает 503
    app.state.ldap = Ldap3Adapter.from_settings(settings) if settings.ldap_url else None
    jira_adapter = _build_jira_adapter(settings)
    app.state.jira_session = JiraSessionService(
        JiraClientSessionAdapter(jira_adapter) if jira_adapter else DisabledJiraSession(),
        SecretStore(repo, settings.enc_key_bytes(), settings.enc_key_id),
    )
    app.state.after_login_hooks = [app.state.jira_session.after_login_hook]
    seed_admins(repo, settings.admin_logins)
    cv_pool = open_cv_pool(settings)
    app.state.sync = build_sync_service(settings, pool, cv_pool, jira_adapter)
    try:
        yield
    finally:
        if cv_pool is not None:
            cv_pool.close()
        pool.close()


app = FastAPI(title="re_web_presale api", lifespan=lifespan)


app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(status_groups_router)
app.include_router(sync_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
