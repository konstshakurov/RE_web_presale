"""Общие вспомогательные объекты QA-тестов входа (KON-56). Секретов нет: пароли ``FAKE_``."""
from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import LAST_ADMIN, NOT_FOUND, UPDATED, AppUser, AppUserDetail
from app.auth import LdapUnavailable, SessionContext, require_admin, router
from app.auth.admin import router as admin_router
from app.auth.sessions import hash_session_id
from app.config import Secret, Settings

FAKE_PASSWORD = "FAKE_qa_password_1"
FAKE_ENC_KEY = base64.b64encode(os.urandom(32)).decode()
NOW = datetime(2026, 1, 1, tzinfo=UTC)
DENIED_TEXT = "Неверный логин или пароль, либо нет доступа"


class MemRepo:
    """Хранение в памяти с теми же контрактами, что у AppRepo (сессии, список, последний админ)."""

    def __init__(self) -> None:
        self.users: dict[str, AppUserDetail] = {}
        self.sessions: dict[bytes, dict] = {}

    def put(self, login, role="user", active=True):
        self.users[login] = AppUserDetail(login, role, active, NOW, NOW)

    def get_user(self, login):
        u = self.users.get(login)
        return AppUser(u.ldap_login, u.role, u.active) if u else None

    def create_session(self, h, login, expires_at):
        self.sessions[h] = {"login": login, "exp": expires_at, "revoked": False}

    def get_active_session(self, h):
        s = self.sessions.get(h)
        if not s or s["revoked"] or s["exp"] <= datetime.now(UTC):
            return None
        return s["login"], s["exp"]

    def touch_session(self, h): ...

    def set_display_name(self, login, name):
        """Появилось в KON-60 (имя из cn при входе); в фейке только запоминаем."""
        self.names = getattr(self, "names", {})
        self.names[login] = name

    def revoke_session(self, h):
        if h in self.sessions:
            self.sessions[h]["revoked"] = True

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


class FakeLdap:
    def __init__(self, accounts: dict[str, str]) -> None:
        self.accounts, self.down, self.calls = accounts, False, []

    def authenticate(self, login, password):
        self.calls.append((login, password))
        if self.down:
            raise LdapUnavailable("down")
        return bool(password) and self.accounts.get(login) == password


def build_app(ldap, repo=None, ttl_hours: float = 8.0, **settings_kw):
    repo = repo or MemRepo()
    app = FastAPI()
    app.include_router(router)
    app.include_router(admin_router)

    @app.post("/admin-probe")
    def admin_probe(ctx: SessionContext = Depends(require_admin)):
        return {"ok": ctx.login}

    app.state.settings = Settings(
        app_db_url=Secret("postgresql://x"), enc_key=Secret(FAKE_ENC_KEY),
        session_cookie_secure=False, session_ttl_hours=ttl_hours, **settings_kw,
    )
    app.state.repo, app.state.ldap, app.state.after_login_hooks = repo, ldap, []
    return app, repo


def login(client: TestClient, name: str, password: str = FAKE_PASSWORD):
    return client.post("/auth/login", json={"login": name, "password": password})


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.get("/auth/me").json()["csrf_token"]}


def session_hash(client: TestClient) -> bytes:
    return hash_session_id(client.cookies.get("re_session"))
