"""Группы статусов на настоящей БД (KON-64): состав, порядок, счётчики /sync/needs/groups.

Нужна TEST_APP_DB_URL (роль app_rw, миграции app_db V001-V007), иначе пропуск.
Данные тестов: префикс KONT64, группы «kont64-*»; после теста удаляются.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from app.app_repo import create_pool
from app.status_groups import CONFLICT, NOT_FOUND, OK, StatusGroupRepo
from app.sync.repo import NeedFilter, SyncRepo

DSN = os.environ.get("TEST_APP_DB_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_APP_DB_URL не задан (стенд БД недоступен)")

P = "KONT64"
S1, S2 = "kont64-статус-1", "kont64-статус-2"


def _clean(pool):
    with pool.connection() as c:
        c.execute("DELETE FROM app.jira_need WHERE jira_key LIKE %s", (P + "-%",))
        c.execute("DELETE FROM app.status_group WHERE name LIKE 'kont64-%'")
        c.execute("DELETE FROM app.status_group_status WHERE status_name LIKE 'kont64-%'")


def _need(pool, n, status):
    now = datetime(2026, 10, 1, tzinfo=UTC)
    with pool.connection() as c:
        c.execute(
            "INSERT INTO app.jira_need (jira_key, title, status, jira_created_at, "
            "jira_updated_at) VALUES (%s, %s, %s, %s, %s)",
            (f"{P}-{n}", f"Заявка {n}", status, now, now),
        )


@pytest.fixture()
def env():
    pool = create_pool(DSN)
    pool.open(wait=True)
    _clean(pool)
    yield pool, StatusGroupRepo(pool), SyncRepo(pool)
    _clean(pool)
    pool.close()


def _count(sync: SyncRepo, group_id: int) -> int:
    return next(g[3] for g in sync.need_group_counts()["groups"] if g[0] == group_id)


def test_create_unique_and_order_at_end(env):
    _, repo, _ = env
    before = max((g.sort_order for g in repo.list_groups()), default=0)
    gid = repo.create_group("kont64-a")
    assert gid is not None
    assert repo.get_group(gid).sort_order == before + 1
    assert repo.create_group("kont64-a") is None
    assert repo.name_taken("KONT64-A") and not repo.name_taken("kont64-a", except_id=gid)


def test_rename_conflict_and_not_found(env):
    _, repo, _ = env
    a, b = repo.create_group("kont64-a"), repo.create_group("kont64-b")
    assert repo.update_group(a, name="kont64-b") == CONFLICT
    assert repo.update_group(a, name="kont64-c") == OK
    assert repo.update_group(-1, name="kont64-x") == NOT_FOUND
    assert repo.get_group(b).name == "kont64-b"


def test_status_membership_idempotent_and_cascade(env):
    _, repo, _ = env
    a, b = repo.create_group("kont64-a"), repo.create_group("kont64-b")
    assert repo.add_status(a, S1) == OK and repo.add_status(a, S1) == OK
    assert repo.add_status(b, S1) == OK  # статус в двух группах
    assert repo.add_status(-1, S1) == NOT_FOUND
    assert repo.get_group(a).statuses == [S1]
    repo.remove_status(a, S1)
    assert repo.get_group(a).statuses == [] and repo.get_group(b).statuses == [S1]
    assert repo.delete_group(b) and not repo.delete_group(b)
    assert repo.get_group(b) is None


def test_counters_follow_changes(env):
    """Критерий приёмки: состав виден в счётчиках и в фильтре потребностей сразу."""
    pool, repo, sync = env
    gid = repo.create_group("kont64-a")
    _need(pool, 1, S1)
    _need(pool, 2, S1)
    _need(pool, 3, S2)
    other0 = sync.need_group_counts()["other"]

    assert _count(sync, gid) == 0
    repo.add_status(gid, S1)
    assert _count(sync, gid) == 2
    assert sync.need_group_counts()["other"] == other0 - 2
    items, total = sync.list_needs(NeedFilter(group=str(gid)), limit=10, offset=0)
    assert total == 2 and {i["status"] for i in items} == {S1}

    repo.add_status(gid, S2)
    assert _count(sync, gid) == 3
    repo.remove_status(gid, S1)
    assert _count(sync, gid) == 1
    assert repo.get_group(gid).need_count == 1
    assert {s.status for s in repo.cache_statuses()} >= {S1, S2}
    # виртуальные группы остаются виртуальными: в таблице нет «Прочие» / «Все статусы»
    with pool.connection() as c:
        n = c.execute(
            "SELECT count(*) FROM app.status_group WHERE name IN ('Прочие', 'Все статусы')"
        ).fetchone()[0]
    assert n == 0


def test_set_order(env):
    _, repo, sync = env
    a, b = repo.create_group("kont64-a"), repo.create_group("kont64-b")
    all_ids = [g.id for g in repo.list_groups()]
    assert not repo.set_order([a, b])  # не все группы
    assert not repo.set_order([*all_ids, *all_ids])
    new = [b, a, *[i for i in all_ids if i not in (a, b)]]
    assert repo.set_order(new)
    assert [g.id for g in repo.list_groups()] == new
    assert [g[0] for g in sync.need_group_counts()["groups"]] == new
    # порядок меняет и фильтр раздела «Потребности»: он идёт по sort_order
