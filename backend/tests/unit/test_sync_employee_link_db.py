"""Миграции V006/V007 и связь потребность-сотрудник на настоящей БД (KON-61).

Нужны TEST_APP_DB_URL (app_rw, V001-V007) и TEST_CV_DB_URL (cv_rw, V001-V006); без них пропуск.
Данные тестов имеют префикс KONT61 и удаляются после теста.
"""
from __future__ import annotations

import os
from dataclasses import replace

import psycopg
import pytest

from app.app_repo import create_pool
from app.sync.mapping import map_employee, map_need
from app.sync.repo import NeedFilter, SyncRepo

from ._sync_fakes import issue

APP_DSN = os.environ.get("TEST_APP_DB_URL")
CV_DSN = os.environ.get("TEST_CV_DB_URL")
pytestmark = pytest.mark.skipif(
    not (APP_DSN and CV_DSN), reason="TEST_APP_DB_URL / TEST_CV_DB_URL не заданы"
)
P = "KONT61"


def _clean(app_pool, cv_pool):
    with app_pool.connection() as c:
        c.execute(f"DELETE FROM app.jira_need WHERE jira_key LIKE '{P}-%'")
    with cv_pool.connection() as c:
        c.execute(f"DELETE FROM cv.employee WHERE jira_key LIKE '{P}-%'")


@pytest.fixture()
def pools():
    app_pool, cv_pool = create_pool(APP_DSN), create_pool(CV_DSN)
    app_pool.open(wait=True)
    cv_pool.open(wait=True)
    _clean(app_pool, cv_pool)
    yield app_pool, cv_pool
    _clean(app_pool, cv_pool)
    app_pool.close()
    cv_pool.close()


@pytest.fixture()
def repo(pools):
    return SyncRepo(*pools)


def need(n, login=None, key=None):
    fields = {"customfield_10707": {"name": login}} if login else {}
    row = map_need(issue(f"{P}-{n}", **fields))
    return row if key is None else replace(row, employee_key=key)


def emp(n, login=None, **fields):
    if login:
        fields["customfield_10707"] = {"name": login}
    return map_employee(issue(f"{P}-{n}", "Свободен", **fields))


def keys(repo):
    items, _ = repo.list_needs(NeedFilter(query=P), limit=50, offset=0)
    return {i["jira_key"]: i["employee_key"] for i in items}


def test_default_status_groups_seeded(pools):
    with pools[0].connection() as c:
        rows = c.execute(
            "SELECT g.name, array_agg(s.status_name ORDER BY s.status_name) "
            "FROM app.status_group g JOIN app.status_group_status s ON s.group_id = g.id "
            "WHERE g.name IN ('Без кандидата', 'Кандидат назначен', 'Интервью', 'Закреплён') "
            "GROUP BY g.name, g.sort_order ORDER BY g.sort_order").fetchall()
    got = {n: set(st) for n, st in rows}
    assert [n for n, _ in rows] == ["Без кандидата", "Кандидат назначен", "Интервью", "Закреплён"]
    assert got["Без кандидата"] == {"Требуется кандидат", "Поиск кандидата", "Найм",
                                    "Отказ по кандидату", "Интервью не пройдено"}
    assert got["Интервью"] == {"Подготовка к интервью", "Интервью состоялось",
                               "Интервью пройдено", "Интервью не пройдено"}
    assert got["Кандидат назначен"] == {"Кандидат назначен"}
    assert got["Закреплён"] == {"Закреплен за проектом"}


def test_employee_fields_round_trip_and_constraints(repo, pools):
    repo.upsert_employees([emp(1, "anna.a", customfield_13504="Опыт",
                               customfield_10814={"value": "Планируется в ближайшие 3 месяца"})])
    items, _ = repo.list_employees(status=None, assignable=None, query=None, limit=500, offset=0)
    it = next(i for i in items if i["jira_key"] == f"{P}-1")
    assert it["extra_experience"] == "Опыт"
    assert it["vacation_plan"] == "Планируется в ближайшие 3 месяца"
    for sql in ("UPDATE cv.employee SET vacation_plan = '' WHERE jira_key = %s",
                "UPDATE cv.employee SET jira_login = 'Anna' WHERE jira_key = %s"):
        with pytest.raises(psycopg.errors.CheckViolation), pools[1].connection() as c:
            c.execute(sql, (f"{P}-1",))


def test_login_map_only_unambiguous_and_reconcile(repo):
    repo.upsert_employees([emp(1, "anna.a"), emp(2, "dup"), emp(3, "dup"), emp(4)])
    mapping = {k: v for k, v in repo.employee_login_map().items() if v.startswith(P)}
    assert mapping == {"anna.a": f"{P}-1"}
    repo.upsert_needs([need(1, "anna.a"), need(2, "dup"), need(3)])
    assert set(keys(repo).values()) == {None}
    repo.reconcile_need_employees(repo.employee_login_map())
    assert keys(repo) == {f"{P}-1": f"{P}-1", f"{P}-2": None, f"{P}-3": None}


def test_upsert_keeps_key_for_same_login_and_resets_on_change(repo):
    repo.upsert_needs([need(1, "anna.a", key=f"{P}-1")])
    repo.upsert_needs([need(1, "anna.a")])  # карту не получили: ключ сохраняется
    assert keys(repo)[f"{P}-1"] == f"{P}-1"
    repo.upsert_needs([need(1, "other")])  # логин сменился: прежний ключ неверен
    assert keys(repo)[f"{P}-1"] is None
    repo.upsert_needs([need(1)])
    with repo._app.connection() as c:  # noqa: SLF001
        assert c.execute("SELECT employee_login FROM app.jira_need WHERE jira_key = %s",
                         (f"{P}-1",)).fetchone()[0] is None
