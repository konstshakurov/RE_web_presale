"""Связи-дубли на настоящей БД (FR-014 v1.1, CR-023): duplicates_for без двойного счёта.

Нужна TEST_APP_DB_URL (роль app_rw, миграции app_db до V013). Без переменной тесты
пропускаются. Данные тестов имеют префикс KONT14, после теста удаляются.
"""
from __future__ import annotations

import os

import pytest
from psycopg_pool import ConnectionPool

from app.app_repo import create_pool
from app.sync.repo import SyncRepo

APP_DSN = os.environ.get("TEST_APP_DB_URL")
pytestmark = pytest.mark.skipif(
    not APP_DSN, reason="TEST_APP_DB_URL не задана"
)

P = "KONT14"


@pytest.fixture()
def app_pool():
    # 127.0.0.1 вместо localhost: иначе IPv6-адрес (::1) без слушателя подвешивает
    # первый коннект воркера пула, и open(wait) завершается PoolTimeout.
    pool = create_pool(APP_DSN.replace("localhost", "127.0.0.1"), min_size=1, max_size=1)
    pool.open(wait=True, timeout=15)
    with pool.connection() as c:
        c.execute(f"DELETE FROM app.need_duplicate WHERE need_key LIKE '{P}-%' "
                  "OR dup_need_key LIKE '{P}-%'")
        c.execute(f"DELETE FROM app.jira_need WHERE jira_key LIKE '{P}-%'")
        c.execute(f"DELETE FROM app.jira_epic WHERE epic_key LIKE '{P}E-%'")
    yield pool
    with pool.connection() as c:
        c.execute(f"DELETE FROM app.need_duplicate WHERE need_key LIKE '{P}-%' "
                  "OR dup_need_key LIKE '{P}-%'")
        c.execute(f"DELETE FROM app.jira_need WHERE jira_key LIKE '{P}-%'")
        c.execute(f"DELETE FROM app.jira_epic WHERE epic_key LIKE '{P}E-%'")


def _need(pool, key: str, title: str, description: str) -> None:
    with pool.connection() as c:
        c.execute(
            "INSERT INTO app.jira_epic (epic_key, name) VALUES (%s, %s) "
            "ON CONFLICT (epic_key) DO NOTHING", (f"{P}E-1", f"{P}E-1"))
        c.execute(
            "INSERT INTO app.jira_need (jira_key, title, description, status, epic_key, "
            "jira_created_at, jira_updated_at) "
            "VALUES (%s, %s, %s, 'Требуется кандидат', %s, now(), now())",
            (key, title, description, f"{P}E-1"))


def _pair(pool, need_key: str, dup_key: str, percent: int) -> None:
    k1, k2 = sorted((need_key, dup_key))
    with pool.connection() as c:
        c.execute(
            "INSERT INTO app.need_duplicate (need_key, dup_need_key, match_percent) "
            "VALUES (%s, %s, %s) ON CONFLICT (need_key, dup_need_key) DO NOTHING",
            (k1, k2, percent))


def test_duplicates_for_both_sides_on_page_no_double_count(app_pool):
    """Обе стороны пары в ключах страницы: связь возвращается каждой стороне ровно один раз,
    с названием ДРУГОЙ стороны (регрессия двойного счёта UNION ALL)."""
    repo = SyncRepo(app_pool)
    _need(app_pool, f"{P}-6012", "Аналитик Team Lead", "описание тб")
    _need(app_pool, f"{P}-6013", "Аналитик Senior", "описание ср")
    _need(app_pool, f"{P}-6014", "Аналитик Прямая", "описание пр")
    _pair(app_pool, f"{P}-6012", f"{P}-6014", 19)
    _pair(app_pool, f"{P}-6013", f"{P}-6014", 20)
    _pair(app_pool, f"{P}-6012", f"{P}-6013", 18)

    keys = [f"{P}-6012", f"{P}-6013", f"{P}-6014"]
    out = repo.duplicates_for(keys)

    # у 6014 ровно две связи, ключи без повторов, названия - «другой стороны»
    d14 = out[f"{P}-6014"]
    assert len(d14) == 2
    assert [d["jira_key"] for d in d14].count(f"{P}-6012") == 1
    assert [d["jira_key"] for d in d14].count(f"{P}-6013") == 1
    by_key = {d["jira_key"]: d for d in d14}
    assert by_key[f"{P}-6012"]["title"] == "Аналитик Team Lead"
    assert by_key[f"{P}-6012"]["match_percent"] == 19
    assert by_key[f"{P}-6013"]["title"] == "Аналитик Senior"
    assert by_key[f"{P}-6013"]["match_percent"] == 20

    # у 6012: 6014 и 6013, названия других сторон
    d12 = out[f"{P}-6012"]
    assert len(d12) == 2
    by12 = {d["jira_key"]: d for d in d12}
    assert by12[f"{P}-6014"]["title"] == "Аналитик Прямая"
    assert by12[f"{P}-6013"]["title"] == "Аналитик Senior"


def test_duplicates_for_empty_keys(app_pool):
    repo = SyncRepo(app_pool)
    assert repo.duplicates_for([]) == {}


def test_classification_updates_canonical_pair(app_pool):
    """Классификация сохраняется на канонической паре независимо от порядка аргументов."""
    repo = SyncRepo(app_pool)
    _need(app_pool, f"{P}-6021", "A", "т")
    _need(app_pool, f"{P}-6022", "B", "т")
    _pair(app_pool, f"{P}-6021", f"{P}-6022", 30)
    assert repo.set_duplicate_classification(f"{P}-6022", f"{P}-6021", "duplicate") is True
    assert repo.set_duplicate_classification(f"{P}-6021", f"{P}-6029", "duplicate") is False
    out = repo.duplicates_for([f"{P}-6021"])
    assert out[f"{P}-6021"][0]["classification"] == "duplicate"
