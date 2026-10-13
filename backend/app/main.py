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
from .cv.limits import CvLimits
from .cv.repo import CvRepo
from .cv.router import router as cv_router
from .jira_session import DisabledJiraSession, JiraClientSessionAdapter, JiraSessionService
from .llm_gateway.factory import build_llm
from .llm_gateway.router import router as llm_router
from .logging_setup import configure_logging
from .matching import build_matching
from .matching.router import router as matching_router
from .secrets import SecretStore
from .status_groups.repo import StatusGroupRepo
from .status_groups.router import router as status_groups_router
from .sync.factory import build_sync_service, open_cv_pool
from .sync.router import router as sync_router
from .validation import install_validation_handler

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
    # файлы CV: только пул cv_rw; лимит файла читается из app_db (app_setting)
    app.state.cv = CvRepo(cv_pool) if cv_pool is not None else None
    app.state.cv_limits = CvLimits(pool, settings.cv_max_file_mb)
    # шлюз LLM (KON-37): обезличивание и постановка задач; вызовы LLM выполняет worker
    llm = build_llm(settings, pool, cv_pool, with_ner=True)
    app.state.llm_repo = llm.repo
    app.state.llm = llm.gateway
    app.state.llm_providers = llm.providers
    app.state.llm_dictionary = llm.dictionary
    # сопоставление и навыки CV (KON-38): задачи ставятся через шлюз, результат собирает worker
    app.state.matching = build_matching(pool, cv_pool, llm.gateway)
    try:
        yield
    finally:
        if cv_pool is not None:
            cv_pool.close()
        pool.close()


app = FastAPI(title="re_web_presale api", lifespan=lifespan)
install_validation_handler(app)  # 422 без введённых значений (KON-68)


app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(status_groups_router)
app.include_router(sync_router)
app.include_router(cv_router)
app.include_router(llm_router)
app.include_router(matching_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
