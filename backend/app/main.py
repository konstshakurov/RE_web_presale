"""api: /health, старт (конфиг, пул app_db, логи, seed админов). Остальное добавляют Dev."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .app_repo import AppRepo, create_pool
from .bootstrap import seed_admins
from .config import load_settings
from .logging_setup import configure_logging

log = logging.getLogger(__name__)


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
    seed_admins(repo, settings.admin_logins)
    try:
        yield
    finally:
        pool.close()


app = FastAPI(title="re_web_presale api", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
