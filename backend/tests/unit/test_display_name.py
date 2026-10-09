"""ФИО из LDAP (KON-60): сохранение при входе, не затирается пустым cn, отдаётся в /auth/me."""
from __future__ import annotations

import base64
import os
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import AppUser
from app.auth import router
from app.auth.ldap_port import LdapProfile
from app.config import Secret, Settings

PW = "FAKE_pw_for_display_name"
CN = "Тестовый Пользователь"


class Repo:
    def __init__(self) -> None:
        self.users = {"ivan": AppUser("ivan", "user", True)}
        self.sessions: dict[bytes, tuple[str, datetime]] = {}

    def get_user(self, login):
        return self.users.get(login)

    def set_display_name(self, login, name):  # как AppRepo: пустое не пишется
        if name.strip():
            u = self.users[login]
            self.users[login] = AppUser(u.ldap_login, u.role, u.active, name.strip())

    def create_session(self, h, login, expires_at):
        self.sessions[h] = (login, expires_at)

    def get_active_session(self, h):
        return self.sessions.get(h)

    def touch_session(self, h):
        pass

    def revoke_session(self, h):
        self.sessions.pop(h, None)


class ProfileLdap:
    def __init__(self) -> None:
        self.cn = CN

    def authenticate_profile(self, login, password):
        if password != PW:
            return None
        return LdapProfile(full_name=self.cn or login, mail="")


@pytest.fixture()
def env():
    repo, ldap = Repo(), ProfileLdap()
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(
        app_db_url=Secret("postgresql://x"),
        enc_key=Secret(base64.b64encode(os.urandom(32)).decode()),
        session_cookie_secure=False,
    )
    app.state.repo, app.state.ldap, app.state.after_login_hooks = repo, ldap, []
    return TestClient(app), repo, ldap


def login(client):
    return client.post("/auth/login", json={"login": "ivan", "password": PW})


def test_login_saves_cn_and_returns_it(env):
    client, repo, _ = env
    assert login(client).json()["display_name"] == CN
    assert repo.users["ivan"].display_name == CN


def test_me_returns_saved_name_after_reload(env):
    client, _, _ = env
    login(client)
    assert client.get("/auth/me").json()["display_name"] == CN


def test_name_updated_on_each_login(env):
    client, repo, ldap = env
    login(client)
    ldap.cn = "Новое Имя"
    assert login(client).json()["display_name"] == "Новое Имя"
    assert repo.users["ivan"].display_name == "Новое Имя"


def test_missing_cn_keeps_previous_name(env):
    client, repo, ldap = env
    login(client)
    ldap.cn = ""  # адаптер подставит логин - это не имя
    r = login(client)
    assert r.status_code == 200
    assert r.json()["display_name"] == CN
    assert repo.users["ivan"].display_name == CN


def test_missing_cn_first_login_gives_null(env):
    client, repo, ldap = env
    ldap.cn = ""
    r = login(client)
    assert r.status_code == 200 and r.json()["display_name"] is None
    assert client.get("/auth/me").json()["display_name"] is None
    assert repo.users["ivan"].display_name is None
