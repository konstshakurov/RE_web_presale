"""Ориентир ставки на настоящей БД (KON-95): НДС, валюты и единицы отдельно, порог 3, период.

Нужен TEST_APP_DB_URL (роль app_rw, миграции app_db V001-V008); без него тесты пропускаются.
Данные тестов имеют префикс KONT95 и уникальные единицы измерения (u95*), после теста удаляются.
Ожидаемые значения посчитаны вручную (в комментариях).
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from app.app_repo import create_pool
from app.sync.rate_benchmark import MIN_GROUP_COUNT, months_ago
from app.sync.repo import SyncRepo

APP_DSN = os.environ.get("TEST_APP_DB_URL")
pytestmark = pytest.mark.skipif(not APP_DSN, reason="TEST_APP_DB_URL не задан")

P = "KONT95"
NOW = datetime.now(UTC)


def _clean(pool):
    with pool.connection() as c:
        c.execute(f"DELETE FROM app.jira_need WHERE jira_key LIKE '{P}-%'")


@pytest.fixture()
def pool():
    p = create_pool(APP_DSN)
    p.open(wait=True)
    _clean(p)
    yield p
    _clean(p)
    p.close()


def put(pool, n, amount, cur="RUB", vat=True, unit="u95", age_days=10):
    created = NOW - timedelta(days=age_days)
    with pool.connection() as c:
        c.execute(
            "INSERT INTO app.jira_need (jira_key, title, status, rate_amount, rate_currency, "
            "rate_includes_vat, rate_unit, jira_created_at, jira_updated_at) "
            "VALUES (%s, 't', 'Требуется кандидат', %s, %s, %s, %s, %s, %s)",
            (f"{P}-{n}", amount, cur, vat if cur == "RUB" else None, unit, created, created),
        )


def bench(pool, months=6, vat=22.0):
    return SyncRepo(pool).rate_benchmark(months_ago(NOW, months), vat, MIN_GROUP_COUNT)


def only(groups, unit):
    return [g for g in groups if g["unit"] == unit]


def test_vat_conversion_mean_and_median(pool):
    # без НДС 1000, 2000, 3000 -> 1220, 2440, 3660; с НДС 1000 остаётся 1000
    put(pool, 1, 1000, vat=False, unit="u95a")
    put(pool, 2, 2000, vat=False, unit="u95a")
    put(pool, 3, 3000, vat=False, unit="u95a")
    put(pool, 4, 1000, vat=True, unit="u95a")
    # 1000, 1220, 2440, 3660: среднее 8320/4 = 2080, медиана (1220 + 2440) / 2 = 1830
    assert only(bench(pool), "u95a") == [
        {"currency": "RUB", "unit": "u95a", "count": 4, "mean": 2080.0, "median": 1830.0}]


def test_vat_percent_is_parameter(pool):
    for n in (1, 2, 3):
        put(pool, n, 1000, vat=False, unit="u95b")
    g = only(bench(pool, vat=20.0), "u95b")[0]
    assert g["mean"] == 1200.0 and g["median"] == 1200.0


def test_currencies_are_separate_and_not_converted(pool):
    for n, a in enumerate((100, 200, 300), 1):
        put(pool, n, a, cur="USD", unit="u95c")
    for n, a in enumerate((1000, 2000, 3000), 4):
        put(pool, n, a, cur="RUB", vat=True, unit="u95c")
    g = only(bench(pool), "u95c")
    assert [(x["currency"], x["count"], x["mean"], x["median"]) for x in g] == [
        ("RUB", 3, 2000.0, 2000.0), ("USD", 3, 200.0, 200.0)]  # USD: без пересчёта НДС


def test_units_are_separate(pool):
    for n, a in enumerate((10, 20, 30), 1):
        put(pool, n, a, unit="u95d1")
    for n, a in enumerate((1000, 2000, 6000), 4):
        put(pool, n, a, unit="u95d2")
    b = bench(pool)
    assert only(b, "u95d1")[0]["mean"] == 20.0
    assert only(b, "u95d2")[0]["mean"] == 3000.0 and only(b, "u95d2")[0]["median"] == 2000.0


def test_group_under_three_has_count_without_mean(pool):
    put(pool, 1, 500, unit="u95e")
    put(pool, 2, 700, unit="u95e")
    assert only(bench(pool), "u95e") == [
        {"currency": "RUB", "unit": "u95e", "count": 2, "mean": None, "median": None}]


def test_period_filters_by_created_and_empty_rate_is_ignored(pool):
    for n in (1, 2, 3):
        put(pool, n, 100, unit="u95f", age_days=300)  # около 10 месяцев назад
    with pool.connection() as c:
        c.execute(
            "INSERT INTO app.jira_need (jira_key, title, status, rate_unit, jira_created_at, "
            "jira_updated_at) VALUES (%s, 't', 'Найм', 'u95f', now(), now())", (f"{P}-9",))
    assert only(bench(pool, 6), "u95f") == []  # пустой период
    g = only(bench(pool, 12), "u95f")[0]
    assert g["count"] == 3  # потребность без ставки не считается


def test_unit_may_be_missing(pool):
    for n in (1, 2, 3):
        put(pool, n, 100, unit=None, cur="KZT")
    g = [x for x in bench(pool) if x["currency"] == "KZT" and x["unit"] is None]
    assert g and g[0]["count"] >= 3


def test_get_setting_missing_key_is_none(pool):
    assert SyncRepo(pool).get_setting("no_such_key_kont95") is None
