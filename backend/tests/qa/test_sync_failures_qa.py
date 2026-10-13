"""KON-63 (Pre.QA-7), кейсы SYNC-05..10: отказы Jira при «Обновить» и в фоновом опросе.

Требования: FR-029 (401 / нет прав / недоступна различаются, кэш и штамп «актуально на» не стираются,
текст отказа Jira как есть, без секретов), FR-034 (``POST /sync/refresh`` для ``needs`` и ``employees``,
ошибки по ``detail.state``: 409/403/503/502), FR-028 (опрос после отказа).
Отказы задаёт фейк Jira (``/__qa/fail``: 401, 403, 400, 500); живая Jira не нужна.
"""
from __future__ import annotations

import subprocess
import time

import pytest

from ._fakes import JiraCtl, requires_fakes, wait_until
from ._stage1 import ensure_allow_list
from ._stand import COMPOSE_PROJECT, PASSWORDS, logged_in, requires_stand
from ._sync_qa import FORBIDDEN_TEXT, refresh, restore_stand, search_calls, stamps

pytestmark = [requires_stand, requires_fakes]

#: режим фейка -> (HTTP /sync/refresh, detail.state, state штампа, часть текста Jira как есть)
MATRIX = {
    "401": (409, "needs_reauthorization", "needs_reauthorization", "not authenticated (fake, scenario)"),
    "403": (403, "no_permission", "no_permission", FORBIDDEN_TEXT),
    "500": (503, "unavailable", "jira_unavailable", "Internal Server Error (fake, scenario)"),
    "400": (502, "blocked", "jira_error", "The JQL query is invalid (fake Jira, QA scenario)"),
}


@pytest.fixture(scope="module")
def jira():
    j = JiraCtl()
    j.reset()
    yield j
    restore_stand(j).close()


@pytest.fixture()
def user(jira):
    """Свежий вход перед каждым кейсом: токен Jira живой, кэш полный, штамп ok."""
    jira.fail("none")
    ensure_allow_list()
    s = logged_in("qa-user")
    jira.reset()
    for scope in ("employees", "needs"):
        r = wait_until(lambda scope=scope: (lambda x: x if x.status_code == 200 and not x.json().get("busy") else None)(
            s.post("/sync/refresh", json={"scope": scope})), timeout=60, interval=2, what=f"refresh {scope}")
        assert r.json()["ok"]
    yield s
    jira.fail("none")
    s.close()


def _snapshot(s) -> dict:
    needs = s.http.get("/sync/needs", params={"limit": 500}).json()
    emps = s.http.get("/sync/employees", params={"limit": 500}).json()
    st = stamps(s)
    return {"needs_total": needs["total"], "needs_keys": [i["jira_key"] for i in needs["items"]],
            "needs_status": {i["jira_key"]: i["status"] for i in needs["items"]},
            "emp_total": emps["total"], "emp_keys": [i["jira_key"] for i in emps["items"]],
            "ok_needs": st["needs"]["last_success_at"], "ok_emp": st["employees"]["last_success_at"]}


@pytest.mark.parametrize("scope", ["needs", "employees"])
@pytest.mark.parametrize("mode", list(MATRIX))
def test_sync05_refresh_refusal_by_detail_state_keeps_cache_and_stamp(user, jira, scope, mode):
    """SYNC-05 (FR-034, FR-029): «Обновить» при отказе Jira отвечает кодом по ``detail.state`` с текстом
    Jira как есть; кэш и штамп «актуально на» не стираются; в списках пометка ``stale`` с тем же текстом."""
    http_code, state, stamp_state, jira_text = MATRIX[mode]
    time.sleep(1)
    before = _snapshot(user)
    assert before["needs_total"] >= 24 and before["emp_total"] == 16
    jira.fail(mode)
    try:
        r = refresh(user, scope)
        assert r.status_code == http_code, (mode, scope, r.status_code, r.text[:300])
        d = r.json()["detail"]
        assert d["state"] == state and d["code"] == stamp_state
        assert jira_text in d["message"], d["message"]            # текст Jira не обобщён
        assert d["message"].strip(), "пустой текст отказа"
        for secret in ("fake-session-", "JSESSIONID", "Cookie", PASSWORDS["qa-user"]):
            assert secret not in d["message"], secret
        after = _snapshot(user)
        for k in ("needs_total", "needs_keys", "needs_status", "emp_total", "emp_keys", "ok_needs", "ok_emp"):
            assert after[k] == before[k], (k, mode, scope)         # кэш и оба штампа прежние
        stamp = stamps(user)[scope]
        assert stamp["state"] == stamp_state and stamp["stale"] is True and stamp["code"] == stamp_state
        assert stamp["message"] == d["message"]                    # в списках тот же текст
        listing = user.http.get("/sync/needs" if scope == "needs" else "/sync/employees",
                                params={"limit": 1}).json()
        assert listing["sync"]["stale"] is True and listing["sync"]["message"] == d["message"]
        # соседняя область не помечена отказом того, что не опрашивалось (кроме фонового цикла)
        assert listing["total"] == before["needs_total" if scope == "needs" else "emp_total"]
    finally:
        jira.fail("none")


def test_sync06_background_401_stops_polling_keeps_cache_and_relogin_recovers(user, jira):
    """SYNC-06 (FR-029, FR-028): истёкшая сессия Jira (401) в фоне -> ``needs_reauthorization``; кэш и
    штамп на месте; после восстановления Jira опрос без нового входа не возобновляется (токен отвергнут,
    Jira не дёргается); повторный вход возобновляет опрос."""
    before = _snapshot(user)
    jira.fail("401")
    try:
        wait_until(lambda: stamps(user)["needs"]["state"] == "needs_reauthorization", timeout=60, interval=2,
                   what="состояние needs_reauthorization в штампе")
        st = stamps(user)["needs"]
        assert st["stale"] is True and st["last_success_at"] == before["ok_needs"]
        assert user.http.get("/sync/needs", params={"limit": 1}).json()["total"] == before["needs_total"]
    finally:
        jira.fail("none")
    time.sleep(8)
    calls = search_calls()
    time.sleep(2.5 * 20)                                          # Jira снова доступна, но токен отвергнут
    assert search_calls() == calls, "опрос обращается к Jira с отвергнутым токеном"
    assert stamps(user)["needs"]["state"] == "needs_reauthorization"
    again = logged_in("qa-user")                                  # повторный вход
    try:
        wait_until(lambda: stamps(again)["needs"]["state"] == "ok", timeout=60, interval=2,
                   what="опрос возобновился после повторного входа")
        assert stamps(again)["needs"]["last_success_at"] > before["ok_needs"]
    finally:
        again.close()


@pytest.mark.parametrize("mode", ["403", "500", "400"])
def test_sync07_background_refusals_are_distinct_and_recover_without_relogin(user, jira, mode):
    """SYNC-07 (FR-029): фоновый опрос различает «нет прав» / «Jira недоступна» / «Jira отклонила запрос»,
    кэш и штамп сохраняются; когда Jira отвечает снова, опрос возобновляется сам (повторный вход не нужен)."""
    _, _, stamp_state, jira_text = MATRIX[mode]
    time.sleep(1)
    before = _snapshot(user)
    jira.fail(mode)
    try:
        wait_until(lambda: stamps(user)["needs"]["state"] == stamp_state, timeout=60, interval=2,
                   what=f"состояние {stamp_state} в штампе")
        st = stamps(user)["needs"]
        assert st["stale"] is True and st["last_success_at"] == before["ok_needs"]
        assert jira_text in st["message"], st["message"]
        assert user.http.get("/sync/needs", params={"limit": 1}).json()["total"] == before["needs_total"]
    finally:
        jira.fail("none")
    wait_until(lambda: stamps(user)["needs"]["state"] == "ok", timeout=60, interval=2,
               what="опрос восстановился без повторного входа")
    st = stamps(user)["needs"]
    assert st["stale"] is False and st["code"] == "" and st["message"] == ""
    assert st["last_success_at"] > before["ok_needs"]


def test_sync08_refusal_during_slow_jira_does_not_block_lists(user, jira):
    """SYNC-08 (FR-034, NFR-008): пока «Обновить» ждёт медленную Jira (отказ через 4 с), списки из кэша
    открываются без ожидания (< 2 с) - чтение не зависит от Jira."""
    import threading

    jira.fail("500", delay=4.0)
    out: dict = {}
    t = threading.Thread(target=lambda: out.update(r=user.post("/sync/refresh", json={"scope": "needs"})))
    t.start()
    try:
        time.sleep(0.8)
        t0 = time.perf_counter()
        r = user.http.get("/sync/needs", params={"limit": 100})
        dt = time.perf_counter() - t0
        assert r.status_code == 200 and dt < 2.0, (r.status_code, dt)
        print(f"SYNC-08: список при зависшей Jira открылся за {dt * 1000:.0f} мс")
    finally:
        t.join(60)
        jira.fail("none")
    assert out["r"].status_code in (503, 200)                      # 200 - busy, если фон держал блокировку


def test_sync09_refresh_without_jira_token_asks_for_reauthorization(user, jira):
    """SYNC-09 (FR-029): у пользователя нет сохранённого токена Jira (сессия приложения жива) ->
    «Обновить» не обращается к Jira и просит повторный вход, а не падает 500."""
    from ._fakes import db

    with db("app_db") as c:
        c.execute("DELETE FROM app.secret_store WHERE owner_login = 'qa-user' AND kind = 'jira_session'")
    calls = search_calls()
    r = refresh(user, "needs")
    # факт на стенде: 409, detail.state = not_checked (статус Jira пользователя не проверен), текст пустой
    assert r.status_code == 409, (r.status_code, r.text[:300])
    assert r.json()["detail"]["state"] in ("not_checked", "needs_reauthorization")
    assert search_calls() == calls
    assert user.http.get("/sync/needs", params={"limit": 1}).status_code == 200


def test_sync10_logs_of_failures_have_no_session_tokens_or_passwords(jira):
    """SYNC-10 (FR-029, NFR-006): в JSON-логах api и worker за время отказов нет значений сессионных токенов
    Jira (cookie ``fake-session-*``) и паролей тестовых пользователей."""
    secrets = [v for v in PASSWORDS.values() if v]
    assert secrets
    for svc in ("api", "worker"):
        out = subprocess.run(["docker", "logs", "--since", "20m", f"{COMPOSE_PROJECT}-{svc}-1"],
                             capture_output=True, text=True, timeout=90, encoding="utf-8", errors="replace")
        text = out.stdout + out.stderr
        assert text.strip(), f"лог {svc} пуст: проверка была бы вхолостую"
        assert "fake-session-" not in text, f"токен Jira в логе {svc}"
        for pw in secrets:
            assert pw not in text, f"пароль в логе {svc}"
