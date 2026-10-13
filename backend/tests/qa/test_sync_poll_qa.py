"""KON-63 (Pre.QA-7), кейсы SYNC-01..04: опрос Jira, пауза без пользователей, возобновление на стенде.

Требования: FR-028 (опрос, кэш, штамп), FR-034 (штамп и пометка в таблице), NFR-008 (актуальность 5 мин;
на стенде интервал опроса потребностей 20 с, сотрудников 30 с, умолчания 300/3600 проверены отдельно).
Фейк Jira и стенд: ``bash qa/stand/stand.sh up``. Живая Jira не нужна. Тесты идут в порядке файла:
SYNC-02 использует состояние паузы, созданное SYNC-01.
"""
from __future__ import annotations

import time

import pytest

from ._fakes import JiraCtl, requires_fakes, wait_until
from ._stage1 import ensure_allow_list, need_by_key, prepare_cache
from ._stand import logged_in, requires_stand
from ._sync_qa import age_sec, pause_everyone, restore_stand, search_calls, stamps

pytestmark = [requires_stand, requires_fakes]

NEEDS_INTERVAL = 20.0


@pytest.fixture(scope="module")
def jira():
    j = JiraCtl()
    j.reset()
    yield j
    restore_stand(j).close()


def test_sync01_all_logged_out_polling_paused_jira_not_called_cache_served(jira):
    """SYNC-01 (FR-028, FR-034): вышли все пользователи -> опрос ``paused``, Jira не вызывается,
    во всех ответах ``/sync/*`` ``sync.paused = true``; список из кэша отдаётся, штамп не стёрт."""
    s0 = logged_in("qa-user")
    prepare_cache(s0, jira)
    before = s0.http.get("/sync/needs", params={"limit": 1}).json()
    assert before["total"] >= 24 and before["sync"]["paused"] is False

    s0.close()
    me = pause_everyone()
    try:
        time.sleep(12)                                # доработать начатый цикл и пересмотр паузы (5 с)
        base = search_calls()
        stamp_before = me.http.get("/sync/needs", params={"limit": 1}).json()["sync"]["last_success_at"]
        assert stamp_before
        for url in ("/sync/needs", "/sync/needs/groups", "/sync/employees"):
            r = me.http.get(url, params={"limit": 1} if url != "/sync/needs/groups" else None)
            assert r.status_code == 200, (url, r.text)
            sync = r.json()["sync"]
            assert sync["paused"] is True and sync["state"] == "paused" and sync["stale"] is True, (url, sync)
        st = stamps(me)
        assert st["needs"]["paused"] and st["employees"]["paused"]
        # штамп «актуально на» и кэш на месте
        # два с лишним интервала опроса потребностей - ни одного обращения к Jira
        time.sleep(2.5 * NEEDS_INTERVAL)
        after = me.http.get("/sync/needs", params={"limit": 1}).json()
        assert after["total"] == before["total"]
        assert after["sync"]["last_success_at"] == stamp_before
        assert search_calls() == base, "при паузе опрос обратился к Jira"
    finally:
        me.close()


def test_sync02_login_resumes_polling_and_data_not_older_than_5_min(jira):
    """SYNC-02 (FR-028, NFR-008): вход пользователя -> опрос стартует сам (без «Обновить»), изменение
    статуса в Jira доходит до кэша, штамп «актуально на» моложе 5 мин, ``paused = false``."""
    jira.set_status("ANALYTICS-1006", "Найм")        # изменение накопилось, пока опрос стоял
    calls = search_calls()
    t0 = time.monotonic()
    s = logged_in("qa-user")                         # вход = новая сессия и токен Jira
    try:
        n = wait_until(lambda: need_by_key(s, "ANALYTICS-1006")["status"] == "Найм" or None,
                       timeout=90, interval=2, what="опрос возобновился и принёс статус")
        elapsed = time.monotonic() - t0
        assert n
        st = stamps(s)["needs"]
        assert st["state"] == "ok" and st["paused"] is False and st["stale"] is False
        assert age_sec(st["last_success_at"]) <= 300
        assert search_calls() > calls
        print(f"SYNC-02: после входа изменение дошло до кэша за {elapsed:.0f} с "
              f"(интервал стенда {NEEDS_INTERVAL:.0f} с, пересмотр паузы 5 с)")
        assert elapsed <= 60
    finally:
        s.close()


def test_sync03_stamp_age_stays_within_interval_while_users_active(jira):
    """SYNC-03 (NFR-008, FR-034): пока есть активные пользователи, штамп «актуально на» не старше
    интервала опроса (+ запас на длительность цикла); данные не залёживаются."""
    ensure_allow_list()
    s = logged_in("qa-user")
    try:
        ages = []
        for _ in range(8):
            st = stamps(s)["needs"]
            assert st["state"] == "ok", st
            ages.append(age_sec(st["last_success_at"]))
            time.sleep(7)
        print(f"SYNC-03: возраст штампа потребностей за 56 с: max={max(ages):.1f} с, "
              f"интервал стенда {NEEDS_INTERVAL:.0f} с, граница NFR-008 300 с")
        assert max(ages) <= NEEDS_INTERVAL + 15
        assert max(ages) <= 300
    finally:
        s.close()


def test_sync04_second_login_does_not_duplicate_polling_cycles(jira):
    """SYNC-04 (FR-028): два пользователя онлайн - один опрос на область за интервал, а не по одному на
    пользователя (в Jira уходит не больше страниц поиска, чем при одном пользователе)."""
    a, b = logged_in("qa-user"), logged_in("qa-admin")
    try:
        time.sleep(3)
        c0 = search_calls()
        time.sleep(3 * NEEDS_INTERVAL)               # три интервала потребностей, 2 интервала сотрудников
        calls = search_calls() - c0
        # потребности: ~3 цикла x 1 страница; сотрудники: ~2 цикла x 1 страница; запас на «Обновить» из других тестов нет
        print(f"SYNC-04: за {3 * NEEDS_INTERVAL:.0f} с при 2 пользователях: {calls} страниц поиска")
        assert calls <= 8
    finally:
        a.close()
        b.close()
