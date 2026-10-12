"""Цикл задач LLM в worker: берёт задачи из очереди app_db и выполняет через шлюз (ADR-007).

Один фоновый поток; ожидание ограничено ``idle_sec``. Пока вызов LLM идёт, основной цикл worker
продолжает обновлять пульс для healthcheck.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .repo import TaskRow
from .service import LlmGateway

log = logging.getLogger(__name__)

PURGE_EVERY_SEC = 3600.0


class LlmRunner:
    def __init__(
        self, gateway: LlmGateway, *, idle_sec: float = 2.0, ttl_days: int = 7,
        clock: Callable[[], float] = time.monotonic,
        on_finished: Callable[[TaskRow], None] | None = None,
    ) -> None:
        self._gw = gateway
        self._idle = idle_sec
        self._ttl_days = ttl_days
        self._clock = clock
        # вызывается после каждой выполненной задачи (результат модуля-заказчика, KON-38);
        # сбой обработчика не должен ронять цикл
        self._on_finished = on_finished
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_purge = 0.0

    def run_once(self) -> bool:
        """Выполнить одну задачу; True, если задача была."""
        try:
            if self._clock() >= self._next_purge:
                self._next_purge = self._clock() + PURGE_EVERY_SEC
                removed = self._gw.purge_old(self._ttl_days)
                if removed:
                    log.info("удалены завершённые задачи LLM", extra={"count": removed})
            task = self._gw.run_next()
            if task is None:
                return False
            if self._on_finished is not None:
                try:
                    self._on_finished(task)
                except Exception as exc:  # noqa: BLE001 - обработчик подберёт задачу при опросе
                    log.error("сбой обработчика задачи LLM", extra={
                        "task_id": task.id, "error": type(exc).__name__})
            return True
        except Exception as exc:  # noqa: BLE001 - БД моргнула: цикл не должен умирать
            log.error("сбой цикла LLM", extra={"error": type(exc).__name__})
            return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._stop.wait(self._idle)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="llm-runner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
