"""Планировщик worker (KON-35): период 5 мин / 1 час, пауза, сбой не останавливает цикл."""
from __future__ import annotations

import pathlib
from concurrent.futures import Future

from app.sync import SyncSettings
from app.sync.repo import SCOPE_EMPLOYEES, SCOPE_NEEDS
from app.sync.scheduler import Scheduler, run_forever
from app.sync.service import SyncResult


class InlineExecutor:
    """Выполняет задачу сразу, в вызывающем потоке."""

    def submit(self, fn, *args):
        f: Future = Future()
        try:
            f.set_result(fn(*args))
        except Exception as exc:  # noqa: BLE001
            f.set_exception(exc)
        return f

    def shutdown(self, **_): ...


class FakeService:
    settings = SyncSettings()

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.paused = False
        self.fail = False

    def poll(self, scope):
        self.calls.append(scope)
        if self.fail:
            raise RuntimeError("сбой")
        return SyncResult(scope=scope, paused=self.paused, ok=not self.paused)


def make():
    clock = {"t": 1000.0}
    svc = FakeService()
    return Scheduler(svc, clock=lambda: clock["t"], executor=InlineExecutor()), svc, clock


def test_both_scopes_run_at_start():
    sch, svc, _ = make()
    assert sorted(sch.tick()) == [SCOPE_EMPLOYEES, SCOPE_NEEDS]


def test_needs_every_5_minutes_employees_every_hour():
    sch, svc, clock = make()
    sch.tick()
    clock["t"] += 299
    assert sch.tick() == []
    clock["t"] += 2
    assert sch.tick() == [SCOPE_NEEDS]
    clock["t"] += 3300  # 3601 с от старта
    assert SCOPE_EMPLOYEES in sch.tick()


def test_paused_poll_is_rechecked_quickly():
    sch, svc, clock = make()
    svc.paused = True
    sch.tick()
    clock["t"] += 16
    svc.paused = False
    assert sorted(sch.tick()) == [SCOPE_EMPLOYEES, SCOPE_NEEDS]  # не ждём 5 минут и час


def test_failure_does_not_stop_scheduler():
    sch, svc, clock = make()
    svc.fail = True
    sch.tick()
    svc.fail = False
    clock["t"] += 301
    assert SCOPE_NEEDS in sch.tick()


def test_running_task_is_not_started_twice():
    class Pending:
        def submit(self, fn, *args):
            return Future()  # никогда не завершается

        def shutdown(self, **_): ...

    clock = {"t": 0.0}
    sch = Scheduler(FakeService(), clock=lambda: clock["t"], executor=Pending())
    assert len(sch.tick()) == 2
    clock["t"] += 10_000
    assert sch.tick() == []


def test_heartbeat_touched_every_step(tmp_path: pathlib.Path):
    sch, svc, _ = make()
    hb = tmp_path / "hb"
    steps = {"n": 0}

    def sleep(_):
        steps["n"] += 1

    run_forever(sch, hb, sleep=sleep, should_stop=lambda: steps["n"] >= 3)
    assert hb.exists() and steps["n"] == 3
