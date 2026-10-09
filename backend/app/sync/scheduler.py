"""Планировщик worker: потребности каждые 5 мин, сотрудники раз в час (ADR-004).

Задачи идут в одном фоновом потоке по очереди, а основной цикл продолжает обновлять пульс
для healthcheck compose, даже если опрос Jira долгий. Без активных пользователей опрос
приостановлен; о появлении нового входа планировщик узнаёт через ``pause_recheck_sec``.
"""
from __future__ import annotations

import logging
import pathlib
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from .repo import SCOPE_EMPLOYEES, SCOPE_NEEDS
from .service import SyncResult, SyncService

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self, service: SyncService, clock: Callable[[], float] = time.monotonic,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        s = service.settings
        self._service = service
        self._clock = clock
        self._interval = {
            SCOPE_NEEDS: s.needs_interval_sec, SCOPE_EMPLOYEES: s.employees_interval_sec,
        }
        self._pause = s.pause_recheck_sec
        # первый опрос сразу при старте
        self._next: dict[str, float] = {SCOPE_NEEDS: 0.0, SCOPE_EMPLOYEES: 0.0}
        self._running: dict[str, Future] = {}
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync")

    def due(self) -> list[str]:
        now = self._clock()
        return [
            scope for scope, at in self._next.items()
            if at <= now and not self._is_running(scope)
        ]

    def _is_running(self, scope: str) -> bool:
        f = self._running.get(scope)
        return f is not None and not f.done()

    def tick(self) -> list[str]:
        """Поставить в очередь просроченные задачи; вернуть их области."""
        started = []
        for scope in self.due():
            # следующий запуск считается от начала: период "раз в N" не дрейфует
            self._next[scope] = self._clock() + self._interval[scope]
            self._running[scope] = self._executor.submit(self._run, scope)
            started.append(scope)
        return started

    def _run(self, scope: str) -> SyncResult | None:
        try:
            res = self._service.poll(scope)
        except Exception:  # noqa: BLE001 - сбой одного опроса не должен останавливать worker
            log.exception("сбой цикла синхронизации", extra={"scope": scope})
            return None
        if res.paused:
            # быстро заметить вход пользователя вместо ожидания полного интервала
            self._next[scope] = min(self._next[scope], self._clock() + self._pause)
        return res

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def run_forever(
    scheduler: Scheduler, heartbeat: pathlib.Path, *, sleep: Callable[[float], None] = time.sleep,
    step_sec: float = 5.0, should_stop: Callable[[], bool] = lambda: False,
) -> None:
    while not should_stop():
        heartbeat.touch()
        scheduler.tick()
        sleep(step_sec)
