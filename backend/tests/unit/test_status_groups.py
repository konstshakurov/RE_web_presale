"""Редактирование групп статусов (KON-64): права, CSRF, валидация, «Прочие». БД не нужна."""
from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import AppUser
from app.auth.sessions import csrf_token, hash_session_id, new_session_id
from app.config import Secret, Settings
from app.status_groups import (
    CONFLICT,
    KNOWN_JIRA_STATUSES,
    NOT_FOUND,
    OK,
    GroupRow,
    StatusRow,
    is_reserved,
    other_statuses,
)
from app.status_groups.router import router

KEY = base64.b64encode(os.urandom(32)).decode()
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeGroupRepo:
    """Репозиторий групп в памяти: те же итоги, что у StatusGroupRepo."""

    def __init__(self) -> None:
        self.groups: dict[int, dict] = {}
        self.cache: dict[str, int] = {}
        self._next = 1

    def seed(self, name, statuses=()):
        gid = self._next
        self._next += 1
        self.groups[gid] = {"name": name, "order": gid, "statuses": set(statuses)}
        return gid

    def _row(self, gid):
        g = self.groups[gid]
        count = sum(c for s, c in self.cache.items() if s in g["statuses"])
        return GroupRow(gid, g["name"], g["order"], sorted(g["statuses"]), count)

    def list_groups(self):
        return sorted((self._row(i) for i in self.groups), key=lambda r: (r.sort_order, r.id))

    def get_group(self, gid):
        return self._row(gid) if gid in self.groups else None

    def cache_statuses(self):
        return [StatusRow(s, c) for s, c in sorted(self.cache.items())]

    def name_taken(self, name, *, except_id=None):
        return any(g["name"].lower() == name.lower() and i != except_id
                   for i, g in self.groups.items())

    def create_group(self, name):
        if self.name_taken(name):
            return None
        gid = self.seed(name)
        self.groups[gid]["order"] = max(g["order"] for g in self.groups.values()) + 1
        return gid

    def update_group(self, gid, *, name=None, sort_order=None):
        if gid not in self.groups:
            return NOT_FOUND
        if name is not None and self.name_taken(name, except_id=gid):
            return CONFLICT
        if name is not None:
            self.groups[gid]["name"] = name
        if sort_order is not None:
            self.groups[gid]["order"] = sort_order
        return OK

    def delete_group(self, gid):
        return self.groups.pop(gid, None) is not None

    def add_status(self, gid, status):
        if gid not in self.groups:
            return NOT_FOUND
        self.groups[gid]["statuses"].add(status)
        return OK

    def remove_status(self, gid, status):
        self.groups[gid]["statuses"].discard(status)

    def set_order(self, ids):
        if sorted(ids) != sorted(self.groups):
            return False
        for pos, gid in enumerate(ids, start=1):
            self.groups[gid]["order"] = pos
        return True


class AuthRepo:
    def __init__(self) -> None:
        self.users = {"boss": "admin", "ivan": "user"}
        self.sessions: dict[bytes, str] = {}

    def get_user(self, login):
        return AppUser(login, self.users[login], True)

    def get_active_session(self, h):
        return (self.sessions[h], NOW) if h in self.sessions else None

    def touch_session(self, h): ...

    def revoke_session(self, h): ...


@pytest.fixture()
def env():
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(KEY))
    app.state.repo = AuthRepo()
    groups = FakeGroupRepo()
    app.state.status_groups = groups
    return app, groups


def client(env, login):
    app, _ = env
    sid = new_session_id()
    app.state.repo.sessions[hash_session_id(sid)] = login
    c = TestClient(app)
    c.cookies.set("re_session", sid)
    return c, {"X-CSRF-Token": csrf_token(app.state.settings.enc_key_bytes(), sid)}


def test_non_admin_403_and_anonymous_401(env):
    c, h = client(env, "ivan")
    assert c.get("/admin/status-groups").status_code == 403
    assert c.post("/admin/status-groups", json={"name": "Новая"}, headers=h).status_code == 403
    assert c.patch("/admin/status-groups/1", json={"name": "X"}, headers=h).status_code == 403
    assert c.delete("/admin/status-groups/1", headers=h).status_code == 403
    assert c.put("/admin/status-groups/order", json={"ids": []}, headers=h).status_code == 403
    assert c.post("/admin/status-groups/1/statuses", json={"status": "X"},
                  headers=h).status_code == 403
    assert env[1].groups == {}
    assert TestClient(env[0]).get("/admin/status-groups").status_code == 401


def test_mutations_require_csrf(env):
    gid = env[1].seed("Без кандидата")
    c, _ = client(env, "boss")
    assert c.post("/admin/status-groups", json={"name": "Новая"}).status_code == 403
    assert c.patch(f"/admin/status-groups/{gid}", json={"name": "X"}).status_code == 403
    assert c.delete(f"/admin/status-groups/{gid}").status_code == 403
    assert c.post(f"/admin/status-groups/{gid}/statuses", json={"status": "X"}).status_code == 403
    assert c.delete(f"/admin/status-groups/{gid}/statuses?status=X").status_code == 403
    assert c.get("/admin/status-groups").status_code == 200  # чтение без CSRF


def test_list_shows_other_statuses_and_known_list(env):
    repo = env[1]
    repo.seed("Интервью", {"Интервью пройдено"})
    repo.cache = {"Интервью пройдено": 3, "Новый статус из Jira": 2, "Отложено": 1}
    c, _ = client(env, "boss")
    body = c.get("/admin/status-groups").json()
    names = {s["status"] for s in body["statuses"]}
    assert set(KNOWN_JIRA_STATUSES) <= names and "Новый статус из Jira" in names
    other = {s["status"]: s["count"] for s in body["other"]}
    assert "Интервью пройдено" not in other
    assert other["Новый статус из Jira"] == 2 and other["Отложено"] == 1
    assert body["other_count"] == 3
    assert body["reserved"] == ["Прочие", "Все статусы"]
    assert body["groups"][0]["need_count"] == 3


def test_create_rename_delete_group(env):
    c, h = client(env, "boss")
    r = c.post("/admin/status-groups", json={"name": "  Моя   группа "}, headers=h)
    assert r.status_code == 201 and r.json()["name"] == "Моя группа"
    gid = r.json()["id"]
    r = c.patch(f"/admin/status-groups/{gid}", json={"name": "Другая"}, headers=h)
    assert r.status_code == 200 and r.json()["name"] == "Другая"
    assert c.delete(f"/admin/status-groups/{gid}", headers=h).status_code == 204
    assert c.delete(f"/admin/status-groups/{gid}", headers=h).status_code == 404


@pytest.mark.parametrize("name", ["Прочие", "все статусы", "  ПРОЧИЕ ", "", "   ", "я" * 129])
def test_reserved_and_bad_names_rejected(env, name):
    c, h = client(env, "boss")
    assert c.post("/admin/status-groups", json={"name": name}, headers=h).status_code == 422
    gid = env[1].seed("Обычная")
    assert c.patch(f"/admin/status-groups/{gid}", json={"name": name},
                   headers=h).status_code == 422
    assert env[1].groups[gid]["name"] == "Обычная"


def test_duplicate_name_409(env):
    repo = env[1]
    repo.seed("Интервью")
    other = repo.seed("Закреплён")
    c, h = client(env, "boss")
    assert c.post("/admin/status-groups", json={"name": "интервью"}, headers=h).status_code == 409
    assert c.patch(f"/admin/status-groups/{other}", json={"name": "Интервью"},
                   headers=h).status_code == 409
    # переименование в то же имя самой группы допустимо
    assert c.patch(f"/admin/status-groups/{other}", json={"name": "Закреплён"},
                   headers=h).status_code == 200


def test_add_and_remove_status(env):
    repo = env[1]
    a, b = repo.seed("A"), repo.seed("B")
    c, h = client(env, "boss")
    r = c.post(f"/admin/status-groups/{a}/statuses", json={"status": " Найм "}, headers=h)
    assert r.status_code == 200 and r.json()["statuses"] == ["Найм"]
    # повтор идемпотентен; тот же статус можно положить в вторую группу
    assert c.post(f"/admin/status-groups/{a}/statuses", json={"status": "Найм"},
                  headers=h).json()["statuses"] == ["Найм"]
    assert c.post(f"/admin/status-groups/{b}/statuses", json={"status": "Найм"},
                  headers=h).status_code == 200
    r = c.delete(f"/admin/status-groups/{a}/statuses", params={"status": "Найм"}, headers=h)
    assert r.status_code == 200 and r.json()["statuses"] == []
    assert repo.groups[b]["statuses"] == {"Найм"}


@pytest.mark.parametrize("status", ["", "   ", "x" * 129])
def test_bad_status_422(env, status):
    gid = env[1].seed("A")
    c, h = client(env, "boss")
    assert c.post(f"/admin/status-groups/{gid}/statuses", json={"status": status},
                  headers=h).status_code == 422


def test_unknown_group_404(env):
    c, h = client(env, "boss")
    assert c.post("/admin/status-groups/99/statuses", json={"status": "X"},
                  headers=h).status_code == 404
    assert c.patch("/admin/status-groups/99", json={"name": "X"}, headers=h).status_code == 404
    assert c.delete("/admin/status-groups/99/statuses", params={"status": "X"},
                    headers=h).status_code == 404


def test_patch_needs_a_field(env):
    gid = env[1].seed("A")
    c, h = client(env, "boss")
    assert c.patch(f"/admin/status-groups/{gid}", json={}, headers=h).status_code == 422


def test_order(env):
    repo = env[1]
    a, b, d = repo.seed("A"), repo.seed("B"), repo.seed("D")
    c, h = client(env, "boss")
    r = c.put("/admin/status-groups/order", json={"ids": [d, a, b]}, headers=h)
    assert r.status_code == 200 and [g["id"] for g in r.json()] == [d, a, b]
    assert c.put("/admin/status-groups/order", json={"ids": [a, b]},
                 headers=h).status_code == 422


def test_rules_helpers():
    assert is_reserved(" прочие ") and is_reserved("Все статусы") and not is_reserved("Интервью")
    assert other_statuses(["b", "a", "c"], ["c"]) == ["a", "b"]
    assert len(KNOWN_JIRA_STATUSES) == 19 and len(set(KNOWN_JIRA_STATUSES)) == 19
