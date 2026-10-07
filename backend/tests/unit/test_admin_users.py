"""Управление списком допущенных (KON-53): права, CSRF, отзыв, защита последнего админа."""
from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import LAST_ADMIN, NOT_FOUND, UPDATED, AppUser, AppUserDetail
from app.auth import router
from app.auth.admin import router as admin_router
from app.auth.sessions import csrf_token, hash_session_id, new_session_id
from app.config import Secret, Settings

KEY = base64.b64encode(os.urandom(32)).decode()
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeRepo:
    def __init__(self) -> None:
        self.users: dict[str, AppUserDetail] = {}
        self.sessions: dict[bytes, dict] = {}

    def put(self, login, role="user", active=True):
        self.users[login] = AppUserDetail(login, role, active, NOW, NOW)

    # auth
    def get_user(self, login):
        u = self.users.get(login)
        return AppUser(u.ldap_login, u.role, u.active) if u else None

    def get_active_session(self, h):
        s = self.sessions.get(h)
        return None if not s or s["revoked"] else (s["login"], NOW)

    def touch_session(self, h): ...

    def revoke_session(self, h):
        self.sessions[h]["revoked"] = True

    # admin
    def list_users_detail(self):
        return sorted(self.users.values(), key=lambda u: u.ldap_login)

    def get_user_detail(self, login):
        return self.users.get(login)

    def add_user(self, login):
        u = self.users.get(login)
        if u is None:
            self.put(login)
            return self.users[login], True
        if not u.active:
            self.put(login, u.role, True)
            return self.users[login], True
        return u, False

    def update_user(self, login, active=None, role=None):
        u = self.users.get(login)
        if u is None:
            return NOT_FOUND
        n_role = u.role if role is None else role
        n_act = u.active if active is None else active
        admins = [x for x in self.users.values() if x.role == "admin" and x.active]
        if u in admins and not (n_role == "admin" and n_act) and len(admins) <= 1:
            return LAST_ADMIN
        self.put(login, n_role, n_act)
        if not n_act:
            for s in self.sessions.values():
                if s["login"] == login:
                    s["revoked"] = True
        return UPDATED


@pytest.fixture()
def env():
    repo = FakeRepo()
    repo.put("boss", "admin")
    repo.put("ivan", "user")
    app = FastAPI()
    app.include_router(router)
    app.include_router(admin_router)
    app.state.settings = Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(KEY))
    app.state.repo = repo
    return app, repo


def session_for(env, login):
    """TestClient с открытой сессией и CSRF-заголовком."""
    app, repo = env
    sid = new_session_id()
    repo.sessions[hash_session_id(sid)] = {"login": login, "revoked": False}
    c = TestClient(app)
    c.cookies.set("re_session", sid)
    tok = csrf_token(app.state.settings.enc_key_bytes(), sid)
    return c, {"X-CSRF-Token": tok}


def section_keys(c):
    return [s["key"] for s in c.get("/auth/me").json()["sections"]]


def test_non_admin_gets_403_everywhere(env):
    c, h = session_for(env, "ivan")
    assert c.get("/admin/users").status_code == 403
    assert c.post("/admin/users", json={"login": "x"}, headers=h).status_code == 403
    assert c.patch("/admin/users/boss", json={"active": False}, headers=h).status_code == 403
    assert env[1].users["boss"].active


def test_anonymous_401(env):
    assert TestClient(env[0]).get("/admin/users").status_code == 401


def test_mutations_require_csrf(env):
    c, _ = session_for(env, "boss")
    assert c.post("/admin/users", json={"login": "new"}).status_code == 403
    assert c.patch("/admin/users/ivan", json={"active": False}).status_code == 403
    assert "new" not in env[1].users and env[1].users["ivan"].active


def test_list_has_fields_no_delete(env):
    c, h = session_for(env, "boss")
    r = c.get("/admin/users")
    assert r.status_code == 200
    assert [u["login"] for u in r.json()] == ["boss", "ivan"]
    assert set(r.json()[0]) == {"login", "role", "active", "created_at", "updated_at"}
    assert c.delete("/admin/users/ivan", headers=h).status_code == 405


def test_add_normalizes_and_is_idempotent(env):
    c, h = session_for(env, "boss")
    r = c.post("/admin/users", json={"login": "  New.User "}, headers=h)
    assert r.status_code == 201 and r.json()["login"] == "new.user"
    assert r.json()["role"] == "user" and r.json()["active"] is True
    again = c.post("/admin/users", json={"login": "NEW.USER"}, headers=h)
    assert again.status_code == 200 and len(env[1].users) == 3


@pytest.mark.parametrize("bad", ["", "   ", "x" * 129])
def test_add_rejects_bad_login(env, bad):
    c, h = session_for(env, "boss")
    assert c.post("/admin/users", json={"login": bad}, headers=h).status_code == 422


def test_revoke_kills_open_session_and_readd_restores(env):
    boss, h = session_for(env, "boss")
    ivan, _ = session_for(env, "ivan")
    assert ivan.get("/auth/me").status_code == 200
    assert boss.patch("/admin/users/ivan", json={"active": False}, headers=h).status_code == 200
    assert ivan.get("/auth/me").status_code == 401
    assert all(s["revoked"] for s in env[1].sessions.values() if s["login"] == "ivan")
    assert "ivan" in env[1].users  # физически не удалён
    r = boss.post("/admin/users", json={"login": "Ivan"}, headers=h)
    assert r.status_code == 201 and r.json()["active"] is True


def test_me_has_no_settings_for_non_admin(env):
    ivan, _ = session_for(env, "ivan")
    boss, _ = session_for(env, "boss")
    assert "settings" not in section_keys(ivan) and "settings" in section_keys(boss)


def test_last_admin_protected_including_self(env):
    c, h = session_for(env, "boss")
    assert c.patch("/admin/users/boss", json={"active": False}, headers=h).status_code == 409
    assert c.patch("/admin/users/boss", json={"role": "user"}, headers=h).status_code == 409
    assert env[1].users["boss"].role == "admin" and env[1].users["boss"].active


def test_second_admin_allows_demote_of_first(env):
    c, h = session_for(env, "boss")
    assert c.patch("/admin/users/ivan", json={"role": "admin"}, headers=h).status_code == 200
    assert c.patch("/admin/users/boss", json={"role": "user"}, headers=h).status_code == 200
    ivan, hi = session_for(env, "ivan")
    assert ivan.patch("/admin/users/ivan", json={"active": False}, headers=hi).status_code == 409


def test_patch_validation(env):
    c, h = session_for(env, "boss")
    assert c.patch("/admin/users/ivan", json={}, headers=h).status_code == 422
    assert c.patch("/admin/users/ivan", json={"role": "root"}, headers=h).status_code == 422
    assert c.patch("/admin/users/nobody", json={"active": False}, headers=h).status_code == 404
