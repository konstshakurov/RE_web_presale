"""worker: планировщик опроса Jira (KON-35, ADR-004) и пульс для healthcheck.

Потребности каждые 5 мин, сотрудники раз в час - токеном активного пользователя (только
чтение); без активных пользователей опрос приостановлен. Пароль в worker не хранится.
Задачи LLM добавляет Dev-6.
"""
from __future__ import annotations

import logging
import pathlib
import signal

from .app_repo import create_pool
from .config import load_settings
from .logging_setup import configure_logging
from .sync.factory import build_jira_adapter, build_sync_service, open_cv_pool
from .sync.scheduler import Scheduler, run_forever
from .sync.settings import load_sync_settings

log = logging.getLogger(__name__)


def main() -> None:
    settings = load_settings()
    configure_logging(settings.secret_values())
    sync_settings = load_sync_settings()
    pool = create_pool(settings.app_db_url.reveal(), max_size=4)
    pool.open(wait=True)
    cv_pool = open_cv_pool(settings)
    jira = build_jira_adapter(settings)
    service = build_sync_service(settings, pool, cv_pool, jira, sync_settings)
    scheduler = Scheduler(service)
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    log.info("worker запущен", extra={"jira_enabled": jira is not None,
                                      "employees_enabled": cv_pool is not None})
    try:
        run_forever(scheduler, pathlib.Path(sync_settings.heartbeat_path),
                    should_stop=lambda: stop["flag"])
    finally:
        scheduler.shutdown()
        if cv_pool is not None:
            cv_pool.close()
        pool.close()


if __name__ == "__main__":
    main()
