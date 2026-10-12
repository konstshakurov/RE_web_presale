"""worker: планировщик опроса Jira (KON-35, ADR-004) и пульс для healthcheck.

Потребности каждые 5 мин, сотрудники раз в час - токеном активного пользователя (только
чтение); без активных пользователей опрос приостановлен. Пароль в worker не хранится.
Задачи LLM (KON-37, ADR-007): очередь llm_task в app_db, отдельный поток; результат
сопоставления и навыков CV подбирает модуль matching (KON-38).
"""
from __future__ import annotations

import logging
import pathlib
import signal

from .app_repo import create_pool
from .config import load_settings
from .llm_gateway import LlmRunner
from .llm_gateway.factory import build_llm
from .logging_setup import configure_logging
from .matching.factory import build_matching, task_handler
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
    llm = build_llm(settings, pool, cv_pool, with_ner=False)
    # результат сопоставления и навыков забирается сразу после задачи (KON-38)
    matching = build_matching(pool, cv_pool, llm.gateway)
    llm_runner = LlmRunner(
        llm.gateway, ttl_days=settings.llm_task_ttl_days,
        on_finished=task_handler(matching) if matching is not None else None)
    llm_runner.start()
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    log.info("worker запущен", extra={"jira_enabled": jira is not None,
                                      "employees_enabled": cv_pool is not None})
    try:
        run_forever(scheduler, pathlib.Path(sync_settings.heartbeat_path),
                    should_stop=lambda: stop["flag"])
    finally:
        llm_runner.stop()
        scheduler.shutdown()
        if cv_pool is not None:
            cv_pool.close()
        pool.close()


if __name__ == "__main__":
    main()
