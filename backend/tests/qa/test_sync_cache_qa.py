"""KON-63 (Pre.QA-7), кейсы SYNC-11..17: кэш Jira - идемпотентность upsert, заметки, ставка, список на
500 записей, фильтры.

Требования: FR-028 (upsert, кэш), FR-034 (таблица, фильтры, limit <= 500), FR-035 (группы статусов),
NFR-002 (рост до 100 потребностей в неделю, чтение из кэша), NFR-008 (списки из кэша <= 2 с),
BR-003 (ставка: допустимые валюты, НДС для RUB). Ожидаемое для фильтров берётся независимым SQL-запросом
к кэшу (роль приложения), а не повторением логики API. Цифры замеров печатаются (``-s``).
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone

import pytest

from ._fakes import JiraCtl, db, requires_fakes, wait_until
from ._stage1 import cleanup_generated_needs, ensure_allow_list, need_by_key, percentile
from ._stand import Session, logged_in, requires_stand
from ._sync_qa import api_sync_events, put_issue, refresh, restore_stand, stamps

pytestmark = [requires_stand, requires_fakes]

LIMIT_SEC = 2.0
TOTAL_TARGET = 500


@pytest.fixture(scope="module")
def jira():
    j = JiraCtl()
    j.reset()
    yield j
    cleanup_generated_needs()
    restore_stand(j).close()


@pytest.fixture(scope="module")
def user(jira):
    ensure_allow_list()
    s = logged_in("qa-user")
    jira.reset()
    _sync_both(s)
    yield s
    s.close()


def _sync_both(s: Session) -> None:
    for scope in ("employees", "needs"):
        r = refresh(s, scope)
        assert r.status_code == 200, r.text


def _all(s: Session, url: str) -> list[dict]:
    items, offset = [], 0
    while True:
        page = s.http.get(url, params={"limit": 500, "offset": offset}).json()
        items += page["items"]
        offset += 500
        if offset >= page["total"]:
            return items


def _stable(items: list[dict]) -> dict[str, dict]:
    """Строки без synced_at и jira_updated_at (оба меняются при перевыгрузке: updated в Jira обновлён)."""
    return {i["jira_key"]: {k: v for k, v in i.items() if k not in ("synced_at", "jira_updated_at")} for i in items}


def _counts() -> dict[str, int]:
    with db("app_db") as c:
        needs = c.execute("SELECT count(*), count(DISTINCT jira_key) FROM app.jira_need").fetchone()
        tags = c.execute("SELECT count(*) FROM app.jira_need_tag").fetchone()[0]
        epics = c.execute("SELECT count(*) FROM app.jira_epic").fetchone()[0]
    with db("cv_db") as c:
        emp = c.execute("SELECT count(*), count(DISTINCT jira_key) FROM cv.employee").fetchone()
    return {"needs": needs[0], "needs_distinct": needs[1], "tags": tags, "epics": epics,
            "emp": emp[0], "emp_distinct": emp[1]}


# --- SYNC-11: идемпотентность -------------------------------------------------------------------------

def test_sync11_repeated_upsert_does_not_duplicate_or_change_data(user, jira):
    """SYNC-11 (FR-028): повторная выгрузка тех же задач (Jira отдаёт их заново, ``updated`` обновлён) не
    создаёт дублей и не меняет данные потребностей и сотрудников; число записей и ключей прежнее."""
    jira.reset()
    _sync_both(user)
    time.sleep(1)
    snap_n = _stable(_all(user, "/sync/needs"))
    snap_e = _stable(_all(user, "/sync/employees"))
    cnt = _counts()
    assert cnt["needs"] == cnt["needs_distinct"] and cnt["emp"] == cnt["emp_distinct"] == 16

    jira.reset()                                           # все задачи «изменились» заново
    rn = refresh(user, "needs")
    re_ = refresh(user, "employees")
    assert rn.status_code == 200 and re_.status_code == 200
    # выгрузка действительно произошла (иначе проверка вхолостую); фон мог забрать часть раньше
    assert rn.json()["busy"] or rn.json()["upserted"] >= 1
    _sync_both(user)                                       # ещё раз: пусть даже пустой инкремент
    time.sleep(1)
    assert _stable(_all(user, "/sync/needs")) == snap_n
    assert _stable(_all(user, "/sync/employees")) == snap_e
    assert _counts() == cnt


# --- SYNC-12: заметки сотрудников --------------------------------------------------------------------

def test_sync12_employee_note_survives_sync_while_status_updates(user, jira):
    """SYNC-12 (FR-028, FR-033): синхронизация обновляет статус сотрудника, но заметка, её автор и время
    не затираются; число карточек не меняется."""
    key = "ANALYTICS-5003"
    stamp = datetime.now(timezone.utc)
    with db("cv_db") as c:
        c.execute("UPDATE cv.employee SET note = %s, note_author = %s, note_updated_at = %s WHERE jira_key = %s",
                  ("Заметка QA KON-63 (вымышленная)", "qa-user", stamp, key))
    try:
        emps = {e["jira_key"]: e for e in _all(user, "/sync/employees")}
        assert emps[key]["note"] == "Заметка QA KON-63 (вымышленная)"
        old_status = emps[key]["status"]
        new_status = "Свободен" if old_status != "Свободен" else "Ожидает подключения"
        jira.set_status(key, new_status)
        r = refresh(user, "employees")
        assert r.status_code == 200 and not r.json()["busy"], r.text
        emps2 = {e["jira_key"]: e for e in _all(user, "/sync/employees")}
        assert len(emps2) == 16
        assert emps2[key]["status"] == new_status != old_status          # синхронизация сработала
        assert emps2[key]["note"] == "Заметка QA KON-63 (вымышленная)"
        assert emps2[key]["note_author"] == "qa-user"
        assert emps2[key]["note_updated_at"] is not None
        with db("cv_db") as c:
            row = c.execute("SELECT note, note_author, note_updated_at FROM cv.employee WHERE jira_key = %s",
                            (key,)).fetchone()
        assert row[0] == "Заметка QA KON-63 (вымышленная)" and row[1] == "qa-user" and row[2] == stamp
    finally:
        with db("cv_db") as c:
            c.execute("UPDATE cv.employee SET note = NULL, note_author = NULL, note_updated_at = NULL "
                      "WHERE jira_key = %s", (key,))
        jira.reset()


# --- SYNC-13: ставка ------------------------------------------------------------------------------

def test_sync13_unsupported_rate_is_dropped_but_need_is_saved(user, jira):
    """SYNC-13 (FR-028, BR-003): ставка в EUR/GBR/GBP и RUB без признака НДС не сохраняется, но задача
    попадает в кэш со всеми остальными полями; допустимые ставки (RUB с НДС/без, USD, KZT) сохраняются;
    число отброшенных ставок видно в журнале синхронизации (``rates_dropped``)."""
    base = {"customfield_20812": {"value": "час"}, "customfield_21105": {"value": "от клиента"},
            "customfield_10101": "CLN-1"}
    cases = {
        "ANALYTICS-29001": ("GBR", 80, "да", None),          # GBR (из требования): отброшена
        "ANALYTICS-29002": ("GBP", 80, "да", None),          # GBP: тоже вне допустимых
        "ANALYTICS-29003": ("EUR", 70, "да", None),
        "ANALYTICS-29004": ("руб.", 3100, None, None),       # RUB без признака НДС: отброшена
        "ANALYTICS-29005": ("руб.", 3200, "нет", ("RUB", 3200.0, False)),
        "ANALYTICS-29006": ("руб.", 3300, "да", ("RUB", 3300.0, True)),
        "ANALYTICS-29007": ("USD", 45, None, ("USD", 45.0, None)),   # USD без НДС допустима (BR-003)
        "ANALYTICS-29008": ("KZT", 90000, "да", ("KZT", 90000.0, True)),
    }
    t0 = time.time()
    for key, (cur, amount, vat, _) in cases.items():
        f = {**base, "customfield_20809": amount, "customfield_20811": {"value": cur}}
        if vat:
            f["customfield_20810"] = {"value": vat}
        put_issue(key, summary=f"Аналитик ставка {cur} {amount}", fields=f)
    # мусор в сумме: задача сохраняется без ставки
    put_issue("ANALYTICS-29009", summary="Аналитик, сумма не число",
              fields={**base, "customfield_20809": "много", "customfield_20811": {"value": "руб."},
                      "customfield_20810": {"value": "да"}})
    put_issue("ANALYTICS-29010", summary="Аналитик, отрицательная сумма",
              fields={**base, "customfield_20809": -5, "customfield_20811": {"value": "USD"}})
    assert refresh(user, "needs").status_code == 200
    want = list(cases) + ["ANALYTICS-29009", "ANALYTICS-29010"]
    wait_until(lambda: all(need_by_key(user, k) for k in want), timeout=60, interval=2,
               what="задачи со ставками в кэше")
    for key, (_, _, _, expect) in cases.items():
        n = need_by_key(user, key)
        assert n is not None, f"{key}: задача не сохранена"             # задача сохраняется всегда
        assert n["status"] == "Требуется кандидат" and n["epic_key"] == "CLN-1"
        rate = n["rate"]
        if expect is None:
            assert (rate["amount"], rate["currency"], rate["includes_vat"]) == (None, None, None), (key, rate)
        else:
            assert (rate["currency"], rate["amount"], rate["includes_vat"]) == expect, (key, rate)
    for key in ("ANALYTICS-29009", "ANALYTICS-29010"):
        r = need_by_key(user, key)["rate"]
        assert (r["amount"], r["currency"], r["includes_vat"]) == (None, None, None), (key, r)
    # журнал: сколько ставок отброшено за этот цикл (GBR, GBP, EUR, RUB без НДС = 4 как минимум)
    time.sleep(2)
    dropped = sum(int(e.get("rates_dropped", 0)) for e in api_sync_events(t0 - 1)
                  if e.get("scope") == "needs" and "rates_dropped" in e)
    print(f"SYNC-13: отброшенных ставок в журнале синхронизации за цикл: {dropped}")
    assert dropped >= 4, dropped


# --- SYNC-14: список на 500 записях ----------------------------------------------------------------

@pytest.fixture(scope="module")
def cache500(user, jira):
    jira.reset()
    _sync_both(user)
    have = user.http.get("/sync/needs", params={"limit": 1}).json()["total"]
    need = TOTAL_TARGET - have
    assert need > 0, have
    t0 = time.perf_counter()
    jira.gen(need, start=20000, status="mixed")
    r = refresh(user, "needs")
    assert r.status_code == 200, r.text
    sync_sec = time.perf_counter() - t0
    total = wait_until(lambda: (lambda t: t if t >= TOTAL_TARGET else None)(
        user.http.get("/sync/needs", params={"limit": 1}).json()["total"]), timeout=60, interval=1,
        what="500 записей в кэше")
    print(f"SYNC-14: подготовка кэша: {total} потребностей, загрузка {need} из Jira за {sync_sec:.1f} с")
    yield total
    cleanup_generated_needs()


def test_sync14_list_of_500_from_cache_under_two_seconds(user, cache500):
    """SYNC-14 (NFR-008, NFR-002, FR-034): при 500 потребностях в кэше страница ``limit=500`` и смесь
    запросов открываются <= 2 с (p95 по 40 замерам одним пользователем; число фиксируется)."""
    total = cache500
    assert total == TOTAL_TARGET
    r = user.http.get("/sync/needs", params={"limit": 500})
    assert r.status_code == 200 and len(r.json()["items"]) == 500 and r.json()["total"] == 500
    full = []
    for _ in range(20):
        t0 = time.perf_counter()
        resp = user.http.get("/sync/needs", params={"limit": 500})
        full.append(time.perf_counter() - t0)
        assert resp.status_code == 200 and len(resp.json()["items"]) == 500
    mix = []
    for params in ({"limit": 100}, {"limit": 100, "sort": "updated", "order": "asc"},
                   {"limit": 100, "group": "other"}, {"limit": 100, "q": "нагрузочный 2"},
                   {"limit": 100, "epic_key": "CLN-2"}, {"limit": 500, "created_from": "2000-01-01"}):
        for _ in range(3):
            t0 = time.perf_counter()
            resp = user.http.get("/sync/needs", params=params)
            mix.append(time.perf_counter() - t0)
            assert resp.status_code == 200
    t0 = time.perf_counter()
    assert user.http.get("/sync/needs/groups").status_code == 200
    groups = time.perf_counter() - t0
    all_t = full + mix + [groups]
    print(f"SYNC-14: 500 записей в кэше. limit=500: p50={statistics.median(full) * 1000:.0f} мс, "
          f"p95={percentile(full, 95) * 1000:.0f} мс, max={max(full) * 1000:.0f} мс; смесь фильтров/сортировок "
          f"(18 запросов): p95={percentile(mix, 95) * 1000:.0f} мс, max={max(mix) * 1000:.0f} мс; "
          f"группы: {groups * 1000:.0f} мс; всего {len(all_t)} замеров, граница {LIMIT_SEC:.0f} с")
    assert percentile(all_t, 95) <= LIMIT_SEC and max(all_t) <= LIMIT_SEC


def test_sync14b_pagination_over_500_is_complete_and_stable(user, cache500):
    """SYNC-14b (FR-034): страницы по 100 по сортировке, где у многих задач одинаковая дата создания, дают
    ровно 500 разных ключей - без пропусков и повторов на границах страниц."""
    for sort, order in (("created", "desc"), ("created", "asc"), ("updated", "desc")):
        keys: list[str] = []
        for offset in range(0, 500, 100):
            page = user.http.get("/sync/needs", params={"limit": 100, "offset": offset, "sort": sort,
                                                         "order": order}).json()
            assert page["total"] == 500 and len(page["items"]) == 100
            keys += [i["jira_key"] for i in page["items"]]
        assert len(set(keys)) == 500, (sort, order, len(set(keys)))


# --- SYNC-15/16: фильтры ---------------------------------------------------------------------------

def _oracle(where: str, params: tuple = ()) -> set[str]:
    with db("app_db") as c:
        return {r[0] for r in c.execute(f"SELECT jira_key FROM app.jira_need WHERE {where}", params).fetchall()}


def _api_keys(s: Session, **params) -> tuple[set[str], int]:
    items, offset, total = [], 0, 0
    while True:
        page = s.http.get("/sync/needs", params={**params, "limit": 500, "offset": offset})
        assert page.status_code == 200, (params, page.text[:200])
        body = page.json()
        items += body["items"]
        total = body["total"]
        offset += 500
        if offset >= total:
            break
    return {i["jira_key"] for i in items}, total


def test_sync15_filters_match_independent_sql_oracle(user, cache500):
    """SYNC-15 (FR-034, FR-035): фильтры group / status / epic_key / q / created_from / created_to
    возвращают ровно то, что даёт независимый SQL по кэшу, и счётчик ``total`` совпадает."""
    with db("app_db") as c:
        groups = c.execute("SELECT id FROM app.status_group").fetchall()
        members = {gid: {r[0] for r in c.execute(
            "SELECT status_name FROM app.status_group_status WHERE group_id = %s", (gid,)).fetchall()}
            for (gid,) in groups}
    assert members
    for gid, statuses in members.items():
        keys, total = _api_keys(user, group=str(gid))
        assert keys == _oracle("status = ANY(%s)", (list(statuses),)) and total == len(keys), gid
    in_any = sorted(set().union(*members.values()))
    keys, total = _api_keys(user, group="other")
    assert keys == _oracle("NOT (status = ANY(%s))", (in_any,)) and total == len(keys)
    keys, total = _api_keys(user, group="all")
    assert total == 500 and len(keys) == 500

    for status in ("Требуется кандидат", "Найм", "Отложено", "Нет такого статуса"):
        keys, total = _api_keys(user, status=status)
        assert keys == _oracle("status = %s", (status,)) and total == len(keys), status
    for epic in ("CLN-1", "CLN-2", "CLN-3", "CLN-404"):
        keys, total = _api_keys(user, epic_key=epic)
        assert keys == _oracle("epic_key = %s", (epic,)) and total == len(keys), epic
    assert _api_keys(user, epic_key="CLN-404") == (set(), 0)

    for q in ("нагрузочный 207", "ANALYTICS-20123", "analytics-20123", "АНАЛИТИК НАГРУЗОЧНЫЙ", "евро"):
        keys, total = _api_keys(user, q=q)
        assert keys == _oracle("(jira_key ILIKE %s OR title ILIKE %s)", (f"%{q}%", f"%{q}%")) \
            and total == len(keys), q
    for wildcard in ("%", "_", "%%%", "\\"):                # подстановочные знаки LIKE ищутся буквально
        keys, total = _api_keys(user, q=wildcard)
        assert total == 0 and keys == set(), wildcard

    day = lambda d: (d.isoformat())                         # noqa: E731
    sql_day = "((jira_created_at AT TIME ZONE 'UTC') + interval '4 hours')::date"
    with db("app_db") as c:
        days = sorted({r[0] for r in c.execute(f"SELECT {sql_day} FROM app.jira_need").fetchall()})
    lo, mid, hi = days[0], days[len(days) // 2], days[-1]
    for frm, to in ((mid, None), (None, mid), (mid, mid), (lo, hi), (hi + timedelta(days=1), None)):
        params = {}
        where, args = ["true"], []
        if frm:
            params["created_from"] = day(frm)
            where.append(f"{sql_day} >= %s")
            args.append(frm)
        if to:
            params["created_to"] = day(to)
            where.append(f"{sql_day} <= %s")
            args.append(to)
        keys, total = _api_keys(user, **params)
        assert keys == _oracle(" AND ".join(where), tuple(args)) and total == len(keys), params


def test_sync15b_filters_combine_with_and(user, cache500):
    """SYNC-15b (FR-034, FR-035): фильтры сочетаются по «И»: группа + эпик + строка + диапазон дат."""
    with db("app_db") as c:
        gid = c.execute("SELECT id FROM app.status_group ORDER BY sort_order LIMIT 1").fetchone()[0]
        sts = [r[0] for r in c.execute(
            "SELECT status_name FROM app.status_group_status WHERE group_id = %s", (gid,)).fetchall()]
    keys, total = _api_keys(user, group=str(gid), epic_key="CLN-1", q="нагрузочный",
                            created_from="2000-01-01", created_to="2100-01-01")
    want = _oracle("status = ANY(%s) AND epic_key = 'CLN-1' AND title ILIKE '%%нагрузочный%%'", (sts,))
    assert keys == want and total == len(want) > 0
    one = user.http.get("/sync/needs", params={"group": str(gid), "epic_key": "CLN-1", "q": "нагрузочный",
                                               "limit": 1}).json()
    assert one["total"] == len(want) and len(one["items"]) == 1


def test_sync16_limit_and_offset_bounds(user, cache500):
    """SYNC-16 (FR-034): ``limit`` 1..500; 0, 501, отрицательные и нечисловые значения - 422 без падения;
    ``offset`` за концом списка - пустая страница с прежним ``total``; то же для сотрудников."""
    ok = user.http.get("/sync/needs", params={"limit": 500})
    assert ok.status_code == 200 and len(ok.json()["items"]) == 500
    assert user.http.get("/sync/needs", params={"limit": 1}).status_code == 200
    for bad in ({"limit": 0}, {"limit": 501}, {"limit": -1}, {"limit": "abc"}, {"offset": -1},
                {"limit": 10_000_000}):
        for url in ("/sync/needs", "/sync/employees"):
            r = user.http.get(url, params=bad)
            assert r.status_code == 422, (url, bad, r.status_code)
    beyond = user.http.get("/sync/needs", params={"limit": 50, "offset": 100_000}).json()
    assert beyond["items"] == [] and beyond["total"] == 500
    emp = user.http.get("/sync/employees", params={"limit": 500}).json()
    assert emp["total"] == 16 and len(emp["items"]) == 16
    assert user.http.get("/sync/employees", params={"limit": 501}).status_code == 422
    # служебные символы в строках фильтров: без 500 и без выполнения SQL
    for q in ("'; DROP TABLE app.jira_need; --", "\x01", "a" * 200):
        r = user.http.get("/sync/needs", params={"q": q, "limit": 5})
        assert r.status_code in (200, 422), (q[:20], r.status_code)
    assert user.http.get("/sync/needs", params={"q": "a" * 201}).status_code == 422
    assert user.http.get("/sync/needs", params={"limit": 1}).json()["total"] == 500


@pytest.mark.parametrize("param", ["q", "status", "epic_key"])
def test_sync16b_nul_byte_in_filter_is_not_500(user, cache500, param):
    """SYNC-16b (FR-034): символ NUL в строковом фильтре - 422 или пустой список, но не 500 (KON-76)."""
    r = user.http.get("/sync/needs", params={param: "a\x00b", "limit": 1})
    assert r.status_code in (200, 422), r.status_code


def test_sync17_weekly_growth_100_needs_poll_and_list_stay_fast(user, jira, cache500):
    """SYNC-17 (NFR-002, NFR-008): прирост +100 потребностей к 500 обрабатывается одним циклом без
    ошибок; после него список ``limit=500`` по-прежнему <= 2 с."""
    jira.gen(100, start=22000, status="Требуется кандидат")
    t0 = time.perf_counter()
    r = refresh(user, "needs")
    dt = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    body = r.json()
    wait_until(lambda: user.http.get("/sync/needs", params={"limit": 1}).json()["total"] >= 600, timeout=60,
               interval=1, what="600 в кэше")
    t1 = time.perf_counter()
    page = user.http.get("/sync/needs", params={"limit": 500})
    list_sec = time.perf_counter() - t1
    assert page.status_code == 200 and len(page.json()["items"]) == 500
    st = stamps(user)["needs"]
    print(f"SYNC-17: +100 потребностей: цикл «Обновить» {dt:.1f} с (fetched={body.get('fetched')}, "
          f"busy={body.get('busy')}); список limit=500 при 600 записях: {list_sec * 1000:.0f} мс; штамп {st['state']}")
    assert st["state"] == "ok" and dt <= 60 and list_sec <= LIMIT_SEC
