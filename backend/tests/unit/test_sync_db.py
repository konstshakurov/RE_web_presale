"""Кэш Jira на настоящей БД (KON-35): upsert, фильтры, группы, скорость, sync_state, сотрудники.

Нужны TEST_APP_DB_URL (роль app_rw, миграции V001-V005) и TEST_CV_DB_URL (роль cv_rw, V001-V005).
Без переменных тесты пропускаются. Данные тестов имеют префикс KONT35, после теста удаляются.
"""
from __future__ import annotations

import base64
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app_repo import AppRepo, create_pool
from app.auth.sessions import hash_session_id, new_session_id
from app.bootstrap import seed_admins
from app.config import Secret, Settings
from app.jira_session.service import JiraStatusInfo
from app.secrets import SecretStore
from app.sync import SyncService, SyncSettings
from app.sync.mapping import map_employee, map_need
from app.sync.repo import SCOPE_EMPLOYEES, SCOPE_NEEDS, NeedFilter, SyncRepo
from app.sync.router import router as sync_router

from ._sync_fakes import FakeJira, issue

APP_DSN = os.environ.get("TEST_APP_DB_URL")
CV_DSN = os.environ.get("TEST_CV_DB_URL")
pytestmark = pytest.mark.skipif(
    not (APP_DSN and CV_DSN), reason="TEST_APP_DB_URL / TEST_CV_DB_URL не заданы"
)

LOGIN = "kont35-user"
FAKE_SEALED = "FAKE_sealed_jira_token_kont35"
KEY = base64.b64encode(os.urandom(32)).decode()
P = "KONT35"


def _clean(app_pool, cv_pool):
    with app_pool.connection() as c:
        c.execute(f"DELETE FROM app.jira_need WHERE jira_key LIKE '{P}-%'")
        c.execute(f"DELETE FROM app.jira_epic WHERE epic_key LIKE '{P}E-%'")
        c.execute("DELETE FROM app.status_group WHERE name LIKE 'kont35-%'")
        c.execute("DELETE FROM app.secret_store WHERE owner_login = %s", (LOGIN,))
        c.execute("DELETE FROM app.app_session WHERE ldap_login = %s", (LOGIN,))
        c.execute("DELETE FROM app.app_user WHERE ldap_login = %s", (LOGIN,))
        c.execute("UPDATE app.sync_state SET last_sync_at = NULL, last_success_at = NULL, "
                  "last_attempt_at = NULL, last_error = NULL, last_error_code = NULL, "
                  "last_error_at = NULL, initial_load_done = false")
    with cv_pool.connection() as c:
        c.execute(f"DELETE FROM cv.employee WHERE jira_key LIKE '{P}-%'")
        c.execute("UPDATE cv.sync_state SET last_sync_at = NULL, last_success_at = NULL, "
                  "last_attempt_at = NULL, last_error = NULL, last_error_code = NULL, "
                  "last_error_at = NULL")


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


def need(n, status="Требуется кандидат", **fields):
    row = map_need(issue(f"{P}-{n}", status, **fields))
    assert row is not None
    return row


def test_migration_v005_column_exists(pools):
    with pools[0].connection() as c:
        assert c.execute("SELECT last_error_code FROM app.sync_state").fetchall() is not None
    with pools[1].connection() as c:
        assert c.execute("SELECT last_error_code FROM cv.sync_state").fetchall() is not None


def test_upsert_is_idempotent_and_keeps_one_row(repo):
    rows = [need(1), need(2, "Кандидат назначен")]
    assert repo.upsert_needs(rows) == 2
    assert repo.upsert_needs(rows) == 2
    items, total = repo.list_needs(NeedFilter(query=P), limit=10, offset=0)
    assert total == 2 and {i["jira_key"] for i in items} == {f"{P}-1", f"{P}-2"}


def test_upsert_updates_status_and_ignores_older_version(repo):
    repo.upsert_needs([need(1, updated="2026-10-09T10:00:00.000+0400")])
    repo.upsert_needs([need(1, "Кандидат назначен", updated="2026-10-09T11:00:00.000+0400")])
    repo.upsert_needs([need(1, "Найм", updated="2026-10-09T09:00:00.000+0400")])  # устаревший
    items, _ = repo.list_needs(NeedFilter(query=P), limit=10, offset=0)
    assert items[0]["status"] == "Кандидат назначен"


def test_rate_and_fields_round_trip(repo):
    repo.upsert_needs([need(
        1, customfield_20809=3050.5, customfield_20810={"value": "Нет"},
        customfield_20811={"value": "руб."}, customfield_20812={"value": "ч (час)"},
        customfield_19201={"value": "Субподрядчик"}, customfield_21105={"value": "Клиент"},
        assignee={"name": "ispolnitel"}, reporter={"name": "autor"},
    )])
    it = repo.list_needs(NeedFilter(query=P), limit=1, offset=0)[0][0]
    assert float(it["rate_amount"]) == 3050.5 and it["rate_currency"] == "RUB"
    assert it["rate_includes_vat"] is False and it["rate_unit"] == "ч (час)"
    assert it["contract_type"] == "Субподрядчик" and it["rate_proposer"] == "от клиента"
    assert it["assignee"] == "ispolnitel" and it["author"] == "autor" and it["tags"] == []


def test_epic_stub_created_and_real_epic_not_overwritten(repo):
    repo.save_epic(f"{P}E-1", "Клиент Бета", "Эпик", [{"id": "x", "name": "NDA", "value": "v"}])
    repo.upsert_needs([need(1, customfield_10101=f"{P}E-1"), need(2, customfield_10101=f"{P}E-2")])
    assert repo.get_epic(f"{P}E-1").name == "Клиент Бета"
    stub = repo.get_epic(f"{P}E-2")
    assert stub.name == f"{P}E-2" and stub.rules_fetched_at is None
    items, total = repo.list_needs(NeedFilter(epic_key=f"{P}E-1"), limit=10, offset=0)
    assert total == 1 and items[0]["epic_name"] == "Клиент Бета"
    assert repo.get_epic(f"{P}E-1").rules[0]["name"] == "NDA"
    assert repo.count_epic_needs(f"{P}E-1") == 1


def test_count_epic_needs_prefix_excludes_other_directions(repo):
    other = map_need(issue("KONTQA-1", "Требуется кандидат", customfield_10101=f"{P}E-1"))
    repo.upsert_needs([need(1, customfield_10101=f"{P}E-1"), other])
    try:
        assert repo.count_epic_needs(f"{P}E-1") == 2
        assert repo.count_epic_needs(f"{P}E-1", f"{P}-") == 1
    finally:
        with repo._app.connection() as c:
            c.execute("DELETE FROM app.jira_need WHERE jira_key = 'KONTQA-1'")


def test_status_groups_filter_other_and_counters(repo, pools):
    repo.upsert_needs([
        need(1, "Требуется кандидат"), need(2, "Интервью не пройдено"),
        need(3, "Кандидат назначен"), need(4, "Отложено"),
    ])
    with pools[0].connection() as c:
        g1 = c.execute("INSERT INTO app.status_group (name, sort_order) VALUES "
                       "('kont35-a', 901) RETURNING id").fetchone()[0]
        g2 = c.execute("INSERT INTO app.status_group (name, sort_order) VALUES "
                       "('kont35-b', 902) RETURNING id").fetchone()[0]
        for g, st in ((g1, "Требуется кандидат"), (g1, "Интервью не пройдено"),
                      (g2, "Интервью не пройдено"), (g2, "Кандидат назначен")):
            c.execute("INSERT INTO app.status_group_status VALUES (%s, %s)", (g, st))
    f = lambda **kw: repo.list_needs(NeedFilter(query=P, **kw), limit=50, offset=0)  # noqa: E731
    assert {i["status"] for i in f(group=str(g1))[0]} == {
        "Требуется кандидат", "Интервью не пройдено"}
    assert f(group=str(g2))[1] == 2  # «Интервью не пройдено» входит в две группы
    assert [i["status"] for i in f(group="other")[0]] == ["Отложено"]
    assert f(group="all")[1] == 4 and f(status="Отложено")[1] == 1
    counts = {g[1]: g[3] for g in repo.need_group_counts()["groups"] if g[1].startswith("kont35")}
    assert counts == {"kont35-a": 2, "kont35-b": 2}  # счётчики пересекаются, не суммируются


def test_search_escapes_like_wildcards(repo):
    repo.upsert_needs([need(1), need(2)])
    assert repo.list_needs(NeedFilter(query="%"), limit=10, offset=0)[1] == 0
    assert repo.list_needs(NeedFilter(query=f"{P}-1"), limit=10, offset=0)[1] == 1


def test_list_of_500_from_cache_is_under_two_seconds(repo):
    base = datetime(2026, 9, 1, tzinfo=UTC)
    rows = []
    for n in range(500):
        stamp = (base + timedelta(minutes=n)).strftime("%Y-%m-%dT%H:%M:00.000+0000")
        rows.append(need(
            n + 1, ["Требуется кандидат", "Найм", "Кандидат назначен"][n % 3],
            created=stamp, updated=stamp, description="Описание " * 50,
            customfield_10101=f"{P}E-{n % 20 + 1}", customfield_10811="Python, FastAPI " * 20,
        ))
    repo.upsert_needs(rows)
    started = time.perf_counter()
    items, total = repo.list_needs(NeedFilter(query=P), limit=500, offset=0)
    counts = repo.need_group_counts()
    elapsed = time.perf_counter() - started
    assert total == 500 and len(items) == 500 and counts["total"] >= 500
    assert elapsed < 2.0, f"список из кэша: {elapsed:.2f} с"
    assert items[0]["jira_key"] == f"{P}-500"  # свежие сверху


def test_sync_state_success_and_failure_with_code(repo):
    t = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    repo.record_attempt_failure(SCOPE_NEEDS, "jira_unavailable", "Jira не отвечает")
    s = repo.get_state(SCOPE_NEEDS)
    assert s.last_error_code == "jira_unavailable" and s.last_error == "Jira не отвечает"
    assert s.last_success_at is None and not s.initial_load_done
    repo.record_attempt_success(SCOPE_NEEDS, t)
    s = repo.get_state(SCOPE_NEEDS)
    assert s.last_error is None and s.last_error_code is None
    assert s.last_sync_at == t and s.last_success_at is not None and s.initial_load_done
    repo.record_attempt_failure(SCOPE_EMPLOYEES, "no_permission", "нет прав")
    assert repo.get_state(SCOPE_EMPLOYEES).last_error_code == "no_permission"
    assert repo.get_state(SCOPE_NEEDS).last_error_code is None


def test_employees_upsert_keeps_note_and_is_idempotent(repo, pools):
    rows = [map_employee(issue(f"{P}-90", "Свободен", customfield_13504="Опыт")),
            map_employee(issue(f"{P}-91", "Уволен"))]
    repo.upsert_employees(rows)
    with pools[1].connection() as c:
        c.execute("UPDATE cv.employee SET note = 'заметка', note_author = 'boss', "
                  "note_updated_at = now() WHERE jira_key = %s", (f"{P}-90",))
    rows[0] = map_employee(issue(f"{P}-90", "Внутренний проект"))
    repo.upsert_employees(rows)
    repo.upsert_employees(rows)
    items, total = repo.list_employees(status=None, assignable=None, query="Заявка",
                                       limit=10, offset=0)
    by_key = {i["jira_key"]: i for i in items}
    assert total >= 2 and by_key[f"{P}-90"]["status"] == "Внутренний проект"
    assert by_key[f"{P}-90"]["note"] == "заметка" and by_key[f"{P}-90"]["note_author"] == "boss"
    assert by_key[f"{P}-91"]["assignable"] is False
    only_free = repo.list_employees(status=None, assignable=True, query="Заявка", limit=50,
                                    offset=0)[0]
    assert f"{P}-91" not in {i["jira_key"] for i in only_free}


def test_advisory_lock_is_exclusive(repo):
    with repo.try_lock(SCOPE_NEEDS) as first:
        assert first
        with repo.try_lock(SCOPE_NEEDS) as second:
            assert second is False
        with repo.try_lock(SCOPE_EMPLOYEES) as other:
            assert other
    with repo.try_lock(SCOPE_NEEDS) as again:
        assert again


def test_pollers_are_active_users_with_live_session_and_token(repo, pools):
    app_repo = AppRepo(pools[0])
    seed_admins(app_repo, (LOGIN,))
    assert repo.list_pollers() == [] or LOGIN not in [p.login for p in repo.list_pollers()]
    store = SecretStore(app_repo, os.urandom(32), "k-test")
    store.save_jira_session(LOGIN, FAKE_SEALED)
    # токен есть, сессии приложения нет -> опрос приостановлен
    assert LOGIN not in [p.login for p in repo.list_pollers()]
    sid = os.urandom(32)
    app_repo.create_session(sid, LOGIN, datetime.now(UTC) + timedelta(hours=8))
    assert LOGIN in [p.login for p in repo.list_pollers()]
    app_repo.revoke_session(sid)
    assert LOGIN not in [p.login for p in repo.list_pollers()]
    sid2 = os.urandom(32)
    app_repo.create_session(sid2, LOGIN, datetime.now(UTC) + timedelta(hours=8))
    with pools[0].connection() as c:
        c.execute("UPDATE app.app_user SET active = false WHERE ldap_login = %s", (LOGIN,))
    assert LOGIN not in [p.login for p in repo.list_pollers()]  # отзыв доступа


def test_end_to_end_poll_with_real_repo_and_encrypted_token(repo, pools):
    """Вход пользователя -> worker находит токен в secret_store -> кэш и штамп в БД."""
    app_repo = AppRepo(pools[0])
    seed_admins(app_repo, (LOGIN,))
    key = os.urandom(32)
    store = SecretStore(app_repo, key, "k-test")
    jira = FakeJira()
    jira.issues = [need_issue(1), need_issue(2)]
    service = SyncService(repo, jira, store, SyncSettings())

    assert service.poll(SCOPE_NEEDS).paused  # активных нет: пауза, Jira не тронута
    assert jira.calls == []
    assert service.sync_info(SCOPE_NEEDS).state == "paused"

    store.save_jira_session(LOGIN, FAKE_SEALED)  # вход пользователя
    app_repo.create_session(os.urandom(32), LOGIN, datetime.now(UTC) + timedelta(hours=8))
    res = service.poll(SCOPE_NEEDS)
    assert res.ok and res.upserted == 2 and jira.calls[0][1] == FAKE_SEALED
    assert service.poll(SCOPE_NEEDS).ok  # повтор не дублирует
    assert repo.list_needs(NeedFilter(query=P), limit=10, offset=0)[1] == 2
    info = service.sync_info(SCOPE_NEEDS)
    assert info.state == "ok" and info.last_success_at is not None
    with pools[0].connection() as c:  # токен в БД зашифрован
        raw = bytes(c.execute("SELECT ciphertext FROM app.secret_store WHERE owner_login = %s",
                              (LOGIN,)).fetchone()[0])
    assert FAKE_SEALED.encode() not in raw


def need_issue(n):
    return issue(f"{P}-{n}", "Требуется кандидат")


def test_api_lists_from_cache_even_when_jira_refuses(repo, pools):
    """Отказ Jira: список отдаётся из кэша с пометкой и текстом отказа как есть."""
    app_repo = AppRepo(pools[0])
    seed_admins(app_repo, (LOGIN,))
    store = SecretStore(app_repo, os.urandom(32), "k-test")
    store.save_jira_session(LOGIN, FAKE_SEALED)
    app_repo.create_session(os.urandom(32), LOGIN, datetime.now(UTC) + timedelta(hours=8))
    jira = FakeJira()
    jira.issues = [need_issue(1)]
    service = SyncService(repo, jira, store, SyncSettings(jira_base_url="https://jira.example"))
    service.poll(SCOPE_NEEDS)

    from app.integrations.jira import CODE_JIRA_UNAVAILABLE, JiraIntegrationError
    jira.error = JiraIntegrationError("Jira не отвечает (таймаут)", code=CODE_JIRA_UNAVAILABLE)
    service.poll(SCOPE_NEEDS)

    app = FastAPI()
    app.include_router(sync_router)
    app.state.settings = Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(KEY))
    app.state.repo = app_repo
    app.state.sync = service
    app.state.jira_session = type("S", (), {"status": lambda self, login: JiraStatusInfo("ok")})()
    sid = new_session_id()
    app_repo.create_session(hash_session_id(sid), LOGIN, datetime.now(UTC) + timedelta(hours=1))
    client = TestClient(app)
    client.cookies.set("re_session", sid)

    r = client.get("/sync/needs", params={"q": P})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and body["items"][0]["jira_key"] == f"{P}-1"
    assert body["items"][0]["jira_url"] == f"https://jira.example/browse/{P}-1"
    assert body["sync"]["stale"] is True and body["sync"]["state"] == "jira_unavailable"
    assert body["sync"]["message"] == "Jira не отвечает (таймаут)"
    assert body["sync"]["last_success_at"] is not None  # штамп последнего успеха сохранён

    g = client.get("/sync/needs/groups").json()
    assert {x["key"] for x in g["groups"]} >= {"other", "all"}
    emp = client.get("/sync/employees")
    assert emp.status_code == 200 and "sync" in emp.json()
    assert client.get("/sync/needs", params={"group": "bad"}).status_code == 422
    assert client.get("/sync/needs", params={"limit": 501}).status_code == 422

