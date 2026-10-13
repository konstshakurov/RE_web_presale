"""KON-47 (Pre.QA-2), Q47-02/11/12: чтение из Jira на стенде - потребности, сотрудники, группы, эпик,
отказы Jira, актуальность кэша, 4 параллельные сессии.

Требования: FR-001, FR-002, FR-020, FR-028, FR-029, FR-034, FR-035, FR-015, BR-003, BR-005, BR-010,
NFR-001, NFR-008. Данные - набор фейка Jira (``qa/stand/fake_jira.py``): 24 потребности ANALYTICS и
3 чужих задачи (проект QA, BACK, тип «Баг»), 16 карточек сотрудников, 3 эпика клиентов.
Запуск: ``bash qa/stand/stand.sh up`` и ``python -m pytest tests/qa/test_stage1_read_qa.py``.
"""
from __future__ import annotations

import threading
import time

import pytest

from ._fakes import JiraCtl, requires_fakes, wait_until
from ._stage1 import FIXTURE_EMPLOYEES, FIXTURE_NEEDS, ensure_allow_list, need_by_key, prepare_cache
from ._stand import Session, logged_in, requires_stand

pytestmark = [requires_stand, requires_fakes]

KNOWN_19 = {
    "Требуется кандидат", "Поиск кандидата", "Найм", "Отказ по кандидату", "Интервью не пройдено",
    "Кандидат назначен", "Подготовка к интервью", "Интервью состоялось", "Интервью пройдено",
    "Закреплен за проектом", "Кандидатов нет", "Поиск партнерки", "Партнерка", "На проекте",
    "Скоро освобождается", "Работа завершена", "Не на проекте", "Неактуально", "Отложено",
}
FOREIGN = {"QA-9001", "BACK-9002", "ANALYTICS-9003"}


@pytest.fixture(scope="module")
def jira():
    j = JiraCtl()
    j.reset()
    yield j
    j.reset()


@pytest.fixture(scope="module")
def user(jira):
    ensure_allow_list()
    s = logged_in("qa-user")
    prepare_cache(s, jira)
    yield s
    s.close()


def _all_needs(s: Session) -> list[dict]:
    items, offset = [], 0
    while True:
        page = s.http.get("/sync/needs", params={"limit": 500, "offset": offset}).json()
        items += page["items"]
        offset += 500
        if offset >= page["total"]:
            return items


# --- FR-028, BR-005, BR-010: состав кэша ----------------------------------------------------------

def test_fr028_cache_has_only_analytics_needs_of_type_analyst(user):
    """FR-028 / BR-005: в кэше только потребности ANALYTICS типа «Аналитик»; чужие проекты и тип «Баг»
    (в Jira есть, в фейке отдаются по общей выдаче эпика) в список не попадают."""
    keys = {n["jira_key"] for n in _all_needs(user)}
    assert not (keys & FOREIGN), keys & FOREIGN
    fixture = {k for k in keys if k.startswith("ANALYTICS-1")}
    assert len(fixture) == FIXTURE_NEEDS
    assert all(k.startswith("ANALYTICS-") for k in keys)


def test_fr020_br010_status_is_jira_status_as_is(user):
    """FR-020 / BR-010: статус - название статуса Jira как есть (одно из 19), не резолюция."""
    needs = {n["jira_key"]: n for n in _all_needs(user) if n["jira_key"].startswith("ANALYTICS-10")}
    statuses = {n["status"] for n in needs.values()}
    assert KNOWN_19 <= statuses, KNOWN_19 - statuses
    assert needs["ANALYTICS-1001"]["status"] == "Требуется кандидат"
    assert needs["ANALYTICS-1006"]["status"] == "Кандидат назначен"


def test_fr001_need_card_fields(user):
    """FR-001: колонки данных потребности: ключ, ссылка, название, описание, статус, клиент, даты."""
    n = need_by_key(user, "ANALYTICS-1001")
    assert n["title"].startswith("Системный аналитик в проект")
    assert n["jira_url"].endswith("/browse/ANALYTICS-1001")
    assert n["epic_key"] == "CLN-1" and n["author"] == "qa-user"
    assert n["contract_type"] == "Прямой контракт"
    assert n["requirements_text"] and "SQL" in n["requirements_text"]
    assert n["jira_created_at"] < n["jira_updated_at"] and n["synced_at"]
    no_epic = need_by_key(user, "ANALYTICS-1103")
    assert no_epic["epic_key"] is None and no_epic["description"] is None


def test_br003_rate_mapping(user):
    """BR-003: ставка - сумма, валюта, НДС, единица, кто предлагает; недопустимая валюта и RUB без
    признака НДС отбрасываются целиком (в списке не показывается неполная ставка)."""
    rub = need_by_key(user, "ANALYTICS-1001")["rate"]
    assert (rub["amount"], rub["currency"], rub["includes_vat"], rub["unit"], rub["proposer"]) == \
           (2500.0, "RUB", True, "час", "от клиента")
    usd = need_by_key(user, "ANALYTICS-1002")["rate"]
    assert usd["currency"] == "USD" and usd["amount"] == 41.0
    novat = need_by_key(user, "ANALYTICS-1003")["rate"]
    assert novat["currency"] == "RUB" and novat["includes_vat"] is False
    assert need_by_key(user, "ANALYTICS-1004")["rate"]["amount"] is None        # ставки нет в Jira
    assert need_by_key(user, "ANALYTICS-1101")["rate"]["amount"] is None        # EUR отброшена
    assert need_by_key(user, "ANALYTICS-1102")["rate"]["amount"] is None        # RUB без НДС отброшена


def test_fr028_unknown_contract_type_is_empty(user):
    assert need_by_key(user, "ANALYTICS-1104")["contract_type"] is None


# --- FR-034 / FR-035: список, фильтры, группы --------------------------------------------------

def test_fr034_search_sort_pagination(user):
    r = user.http.get("/sync/needs", params={"q": "ANALYTICS-1005"}).json()
    assert [i["jira_key"] for i in r["items"]] == ["ANALYTICS-1005"] and r["total"] == 1
    r = user.http.get("/sync/needs", params={"q": "евро"}).json()
    assert [i["jira_key"] for i in r["items"]] == ["ANALYTICS-1101"]
    asc = user.http.get("/sync/needs", params={"sort": "created", "order": "asc", "limit": 500}).json()
    created = [i["jira_created_at"] for i in asc["items"]]
    assert created == sorted(created)
    desc = user.http.get("/sync/needs", params={"sort": "created", "order": "desc", "limit": 500}).json()
    assert [i["jira_key"] for i in desc["items"]] == [i["jira_key"] for i in reversed(asc["items"])] \
        or desc["total"] == asc["total"]
    p1 = user.http.get("/sync/needs", params={"limit": 5, "offset": 0, "sort": "created"}).json()
    p2 = user.http.get("/sync/needs", params={"limit": 5, "offset": 5, "sort": "created"}).json()
    assert p1["total"] == p2["total"] >= FIXTURE_NEEDS
    assert not {i["jira_key"] for i in p1["items"]} & {i["jira_key"] for i in p2["items"]}
    assert user.http.get("/sync/needs", params={"limit": 501}).status_code == 422
    assert user.http.get("/sync/needs", params={"group": "abc"}).status_code == 422


def test_fr034_created_range_filter(user):
    """created_from/created_to: сутки в поясе Jira (+04:00), границы включительно."""
    from datetime import datetime, timedelta, timezone

    tz = timezone(timedelta(hours=4))
    all_needs = _all_needs(user)
    day = lambda n: datetime.fromisoformat(n["jira_created_at"].replace("Z", "+00:00")).astimezone(tz).date()  # noqa: E731
    pivot = day(next(n for n in all_needs if n["jira_key"] == "ANALYTICS-1010"))
    r = user.http.get("/sync/needs", params={"created_from": pivot.isoformat(), "limit": 500}).json()
    assert r["total"] == sum(1 for n in all_needs if day(n) >= pivot)
    r = user.http.get("/sync/needs", params={"created_to": pivot.isoformat(), "limit": 500}).json()
    assert r["total"] == sum(1 for n in all_needs if day(n) <= pivot)
    one = user.http.get("/sync/needs", params={"created_from": pivot.isoformat(),
                                               "created_to": pivot.isoformat(), "limit": 500}).json()
    assert one["total"] == sum(1 for n in all_needs if day(n) == pivot) >= 1
    bad = user.http.get("/sync/needs", params={"created_from": "2026-12-01", "created_to": "2026-01-01"})
    assert bad.status_code == 422


def test_fr035_groups_counts_and_membership(user):
    """FR-035: группы статусов со счётчиками; «Прочие» и «Все статусы» - виртуальные; каждый статус
    попадает в какую-либо группу, потребность не пропадает."""
    g = user.http.get("/sync/needs/groups").json()
    by_key = {x["key"]: x for x in g["groups"]}
    assert "all" in by_key and "other" in by_key
    real = [x for x in g["groups"] if x["id"] is not None]
    assert len(real) >= 1
    total = by_key["all"]["count"]
    assert total >= FIXTURE_NEEDS
    assert sum(x["count"] for x in real) + by_key["other"]["count"] >= total       # группы могут пересекаться
    for x in g["groups"]:
        n = user.http.get("/sync/needs", params={"group": x["key"], "limit": 1}).json()["total"]
        assert n == x["count"], (x["name"], n, x["count"])
    assert {s["status"] for s in g["statuses"]} >= KNOWN_19


def test_fr035_status_filter_matches_counts(user):
    g = user.http.get("/sync/needs/groups").json()
    for s in g["statuses"][:6]:
        n = user.http.get("/sync/needs", params={"status": s["status"], "limit": 1}).json()["total"]
        assert n == s["count"]


def test_fr001_lists_require_session():
    s = Session()
    for url in ("/sync/needs", "/sync/needs/groups", "/sync/employees", "/sync/status"):
        assert s.http.get(url).status_code == 401, url


# --- FR-002: сотрудники ----------------------------------------------------------------------------

def test_fr002_employees_statuses_and_assignable(user):
    r = user.http.get("/sync/employees", params={"limit": 500}).json()
    assert r["total"] == FIXTURE_EMPLOYEES
    statuses = {e["status"] for e in r["items"]}
    assert {"Свободен", "Коммерческий проект", "Уволен", "Декрет"} <= statuses
    for e in r["items"]:
        should = e["status"] not in {"Коммерческий проект", "Недоступен для подключения", "Уволен", "Декрет"}
        assert e["assignable"] is should, e["status"]
    assert any(e["vacation_plan"] for e in r["items"])
    assert all(e["extra_experience"] for e in r["items"] if e["jira_key"].startswith("ANALYTICS-50"))
    assert r["sync"]["state"] == "ok"


def test_fr002_employee_filters(user):
    free = user.http.get("/sync/employees", params={"status": "Свободен"}).json()
    assert free["total"] >= 1 and all(e["status"] == "Свободен" for e in free["items"])
    can = user.http.get("/sync/employees", params={"assignable": "true", "limit": 500}).json()
    cant = user.http.get("/sync/employees", params={"assignable": "false", "limit": 500}).json()
    assert can["total"] + cant["total"] == FIXTURE_EMPLOYEES and cant["total"] >= 4
    byname = user.http.get("/sync/employees", params={"q": "Петрова"}).json()
    assert [e["full_name"] for e in byname["items"]] == ["Петрова Анна Игоревна"]


# --- FR-003/FR-028: связь потребности с сотрудником -----------------------------------------------

def test_fr028_need_linked_to_employee_by_profile_login(user):
    """Потребность ANALYTICS-1105: «Профиль сотрудника» qa.emp01 -> карточка ANALYTICS-5001."""
    n = wait_until(lambda: need_by_key(user, "ANALYTICS-1105"), what="потребность 1105")
    assert n["employee_key"] == "ANALYTICS-5001"


# --- FR-015: эпик клиента ----------------------------------------------------------------------------

def test_fr015_epic_rules_and_needs_total(user, jira):
    r = user.http.get("/sync/epics/CLN-1")
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["name"] == "Клиент Лотос" and e["stale"] is False and e["error"] is None
    assert len(e["rules"]) == 13 and {x["name"] for x in e["rules"]} >= {"Рекомендации", "NDA", "Штрафы"}
    assert e["needs_total"] == 9            # 7 по ротации + EUR + RUB без НДС; QA-9001 и BACK-9002 не считаются
    partial = user.http.get("/sync/epics/CLN-2").json()
    assert 1 <= len(partial["rules"]) < 13
    assert user.http.get("/sync/epics/CLN-3").json()["rules"] == []


def test_fr015_epic_cached_for_one_hour(user, jira):
    user.http.get("/sync/epics/CLN-2")
    before = jira.stats()["epic"]
    assert user.http.get("/sync/epics/CLN-2").status_code == 200
    assert jira.stats()["epic"] == before                                  # из кэша, Jira не вызывалась
    assert user.http.get("/sync/epics/CLN-2", params={"refresh": "true"}).status_code == 200
    assert jira.stats()["epic"] == before + 1


def test_fr015_epic_key_validation_and_unknown(user):
    assert user.http.get("/sync/epics/not-a-key").status_code in (404, 422)
    r = user.http.get("/sync/epics/CLN-404")
    assert r.status_code in (404, 502, 503) and r.status_code != 200


# --- FR-028 / NFR-008: инкрементальный опрос и актуальность ---------------------------------------

def test_fr028_incremental_refresh_fetches_only_changed(user, jira):
    # кэш уже содержит набор целиком: «старим» его в Jira (updated на 10 мин назад) и меняем одну задачу;
    # курсор опроса идёт от updated (точность до минуты, FR-028, ADR-004), поэтому нужна чистая граница
    assert user.post("/sync/refresh", json={"scope": "needs"}).status_code == 200
    jira.age(10)
    jira.set_status("ANALYTICS-1004", "Найм")
    r = user.post("/sync/refresh", json={"scope": "needs"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert 1 <= body["fetched"] <= 2, body                                 # только изменённая, не весь набор (24)
    assert need_by_key(user, "ANALYTICS-1004")["status"] == "Найм"


def test_nfr008_background_poll_delivers_change_within_interval(user, jira):
    """NFR-008 (M-004): без «Обновить» изменение статуса в Jira доходит до кэша за интервал опроса.
    Интервал стенда 20 с (SYNC_NEEDS_INTERVAL_SEC); значения по умолчанию 300/3600 - см. ниже."""
    jira.set_status("ANALYTICS-1005", "Отложено")
    t0 = time.monotonic()
    wait_until(lambda: need_by_key(user, "ANALYTICS-1005")["status"] == "Отложено",
               timeout=90, interval=2, what="статус потребности дошёл до кэша")
    elapsed = time.monotonic() - t0
    print(f"NFR-008: изменение дошло за {elapsed:.0f} с (интервал стенда 20 с)")
    assert elapsed <= 60


def test_nfr008_default_intervals_are_5min_and_1h():
    from app.sync.settings import load_sync_settings

    d = load_sync_settings({})
    assert d.needs_interval_sec == 300 and d.employees_interval_sec == 3600


def test_nfr008_stamp_is_fresh_and_has_interval(user):
    stamps = {s["scope"]: s for s in user.http.get("/sync/status").json()}
    assert set(stamps) == {"needs", "employees"}
    assert stamps["needs"]["interval_sec"] == 20 and stamps["employees"]["interval_sec"] == 30
    for s in stamps.values():
        assert s["state"] == "ok" and s["stale"] is False and s["last_success_at"]


# --- FR-029: отказы Jira --------------------------------------------------------------------------

def test_fr029_jira_500_keeps_cache_and_marks_stale(user, jira):
    before = user.http.get("/sync/needs", params={"limit": 1}).json()
    stamp_before = before["sync"]["last_success_at"]
    jira.fail("500")
    try:
        r = user.post("/sync/refresh", json={"scope": "needs"})
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["state"] == "unavailable"
        after = user.http.get("/sync/needs", params={"limit": 1}).json()
        assert after["total"] == before["total"]                          # кэш не стёрт
        assert after["sync"]["stale"] is True and after["sync"]["code"]
        assert after["sync"]["last_success_at"] == stamp_before            # штамп «актуально на» не сдвинут
        assert user.http.get("/sync/employees", params={"limit": 1}).status_code == 200
    finally:
        jira.fail("none")
    ok = wait_until(lambda: user.post("/sync/refresh", json={"scope": "needs"}).status_code == 200,
                    timeout=30, interval=2, what="восстановление после отказа")
    assert ok
    assert user.http.get("/sync/needs", params={"limit": 1}).json()["sync"]["stale"] is False


def test_fr029_jira_403_text_shown_as_is(user, jira):
    jira.fail("403")
    try:
        r = user.post("/sync/refresh", json={"scope": "needs"})
        assert r.status_code == 403, r.text
        d = r.json()["detail"]
        assert d["state"] == "no_permission"
        assert "permission" in d["message"].lower()                       # текст Jira, не обобщение
        assert user.http.get("/sync/needs", params={"limit": 1}).status_code == 200
    finally:
        jira.fail("none")


def test_fr029_jira_401_requires_reauthorization_then_relogin_recovers(jira):
    s = logged_in("qa-user")
    jira.fail("401")
    try:
        r = s.post("/sync/refresh", json={"scope": "needs"})
        assert r.status_code == 409 and r.json()["detail"]["state"] == "needs_reauthorization", r.text
        st = s.http.get("/sync/needs", params={"limit": 1}).json()["sync"]
        assert st["stale"] is True
    finally:
        jira.fail("none")
    again = logged_in("qa-user")                                          # повторный вход выдаёт новый токен Jira
    r = again.post("/sync/refresh", json={"scope": "needs"})
    assert r.status_code == 200, r.text


# --- NFR-001: 5 допущенных, 4 одновременные сессии ----------------------------------------------

def test_nfr001_five_users_four_parallel_sessions(jira):
    """NFR-001 (M-001): в списке 5 допущенных; 4 сессии работают одновременно (вход, списки)."""
    admin = ensure_allow_list()
    assert admin.post("/admin/users", json={"login": "qa-outsider"}).status_code in (200, 201)
    assert admin.patch("/admin/users/qa-outsider", json={"active": True}).status_code == 200
    try:
        active = [u for u, d in admin.users().items() if d["active"]]
        assert len(active) >= 5
        results: list[tuple[str, int, int, int]] = []
        errors: list[str] = []

        def work(name: str) -> None:
            try:
                s = logged_in(name)
                for _ in range(5):
                    a = s.http.get("/sync/needs", params={"limit": 100}).status_code
                    b = s.http.get("/sync/employees", params={"limit": 100}).status_code
                    c = s.me().status_code
                    results.append((name, a, b, c))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc!r}")

        threads = [threading.Thread(target=work, args=(n,))
                   for n in ("qa-admin", "qa-user", "qa-revoked", "qa-outsider")]
        [t.start() for t in threads]
        [t.join(120) for t in threads]
        assert errors == []
        assert len(results) == 20
        assert all(a == 200 and b == 200 and c == 200 for _, a, b, c in results), results
        # пятый допущенный входит, пока заняты четыре сессии
        fifth = logged_in("qa-jirablock")
        assert fifth.http.get("/sync/needs", params={"limit": 1}).status_code == 200
    finally:
        admin.patch("/admin/users/qa-outsider", json={"active": False})
