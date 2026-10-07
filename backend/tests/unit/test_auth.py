"""Вход, сессии, CSRF, список допущенных (KON-51). БД и LDAP заменены фейками, сеть не нужна."""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import AppUser
from app.auth import (
    LdapUnavailable,
    SessionContext,
    authenticated_csrf,
    require_admin,
    router,
)
from app.auth.ldap_adapter import Ldap3Adapter, build_filter
from app.auth.sessions import hash_session_id
from app.config import ConfigError, Secret, Settings
from app.logging_setup import configure_logging

PASSWORD = "Pw-Zq9!unit-test-7731"
KEY = base64.b64encode(os.urandom(32)).decode()
DENIED = {"detail": "Неверный логин или пароль, либо нет доступа"}


class FakeRepo:
    """Те же методы, что у AppRepo, хранение в памяти."""

    def __init__(self) -> None:
        self.users: dict[str, AppUser] = {}
        self.sessions: dict[bytes, dict] = {}

    def get_user(self, login):
        return self.users.get(login)

    def create_session(self, h, login, expires_at):
        self.sessions[h] = {"login": login, "exp": expires_at, "revoked": False, "seen": None}

    def get_active_session(self, h):
        s = self.sessions.get(h)
        if not s or s["revoked"] or s["exp"] <= datetime.now(UTC):
            return None
        return s["login"], s["exp"]

    def touch_session(self, h):
        self.sessions[h]["seen"] = datetime.now(UTC)

    def revoke_session(self, h):
        if h in self.sessions:
            self.sessions[h]["revoked"] = True


class FakeLdap:
    def __init__(self, accounts: dict[str, str]) -> None:
        self.accounts = accounts
        self.down = False
        self.calls: list[str] = []

    def authenticate(self, login, password):
        self.calls.append(login)
        if self.down:
            raise LdapUnavailable("down")
        return bool(password) and self.accounts.get(login) == password


def make_settings(**kw) -> Settings:
    return Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(KEY), **kw)


@pytest.fixture()
def env():
    repo = FakeRepo()
    repo.users["ivan"] = AppUser("ivan", "user", True)
    repo.users["boss"] = AppUser("boss", "admin", True)
    ldap = FakeLdap({"ivan": PASSWORD, "boss": PASSWORD, "outsider": PASSWORD})
    app = FastAPI()
    app.include_router(router)

    @app.post("/mutate")
    def mutate(ctx: SessionContext = Depends(authenticated_csrf)):
        return {"ok": ctx.login}

    @app.post("/admin-only")
    def admin_only(ctx: SessionContext = Depends(require_admin)):
        return {"ok": ctx.login}

    app.state.settings = make_settings(session_cookie_secure=False)
    app.state.repo = repo
    app.state.ldap = ldap
    app.state.after_login_hooks = []
    return app, repo, ldap


@pytest.fixture()
def client(env):
    return TestClient(env[0])


def login(client, name="ivan", password=PASSWORD):
    return client.post("/auth/login", json={"login": name, "password": password})


def only_session(repo):
    return next(iter(repo.sessions.values()))


def test_login_ok_sets_cookie_and_stores_only_hash(client, env):
    r = login(client)
    assert r.status_code == 200
    sid = client.cookies.get("re_session")
    assert sid
    assert hash_session_id(sid) in env[1].sessions
    assert sid.encode() not in b"".join(env[1].sessions)
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header
    assert "max-age=28800" in header
    assert r.json()["login"] == "ivan"


def test_cookie_secure_flag_from_settings(env):
    app = env[0]
    app.state.settings = make_settings(session_cookie_secure=True)
    r = login(TestClient(app, base_url="https://testserver"))
    assert "secure" in r.headers["set-cookie"].lower()


def test_login_is_case_insensitive(client):
    assert login(client, "  IVAN ").status_code == 200


def test_not_in_list_denied_no_session(client, env):
    r = login(client, "outsider")
    assert r.status_code == 401
    assert not env[1].sessions
    assert "set-cookie" not in r.headers


def test_inactive_user_denied(client, env):
    env[1].users["ivan"] = AppUser("ivan", "user", False)
    assert login(client).status_code == 401
    assert not env[1].sessions


def test_wrong_password_and_not_in_list_same_text(client):
    a = login(client, "ivan", "wrong-password-1").json()
    b = login(client, "outsider").json()
    c = login(client, "nobody-at-all").json()
    assert a == b == c == DENIED


def test_empty_password_denied_without_ldap_call(client, env):
    assert login(client, "ivan", "").status_code == 401
    assert env[2].calls == []


def test_empty_login_denied(client):
    assert login(client, "  ", PASSWORD).status_code == 401


def test_ldap_down_gives_503_without_details(client, env):
    env[2].down = True
    r = login(client)
    assert r.status_code == 503
    assert "down" not in r.text and not env[1].sessions


def test_ldap_not_configured_gives_503(env):
    env[0].state.ldap = None
    assert login(TestClient(env[0])).status_code == 503


def test_me_returns_sections_by_role(client):
    login(client, "ivan")
    me = client.get("/auth/me").json()
    assert me["role"] == "user"
    assert [s["key"] for s in me["sections"]] == ["needs", "employees", "resume"]
    assert me["jira_status"]["state"] == "not_checked"
    admin = TestClient(client.app)
    login(admin, "boss")
    sections = admin.get("/auth/me").json()["sections"]
    assert sections[-1] == {"key": "settings", "title": "Настройки", "admin_only": True}


def test_me_without_cookie_401(client):
    assert client.get("/auth/me").status_code == 401


def test_session_updates_last_seen(client, env):
    login(client)
    client.get("/auth/me")
    assert only_session(env[1])["seen"] is not None


def test_deactivated_user_gets_401_on_next_request(client, env):
    login(client)
    assert client.get("/auth/me").status_code == 200
    env[1].users["ivan"] = AppUser("ivan", "user", False)
    assert client.get("/auth/me").status_code == 401
    assert only_session(env[1])["revoked"]


def test_removed_user_gets_401(client, env):
    login(client)
    del env[1].users["ivan"]
    assert client.get("/auth/me").status_code == 401


def test_expired_session_invalid(client, env):
    login(client)
    only_session(env[1])["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get("/auth/me").status_code == 401


def test_logout_revokes_session(client):
    token = login(client).json()["csrf_token"]
    sid = client.cookies.get("re_session")
    r = client.post("/auth/logout", headers={"X-CSRF-Token": token})
    assert r.status_code == 204
    client.cookies.set("re_session", sid)  # старый cookie после logout недействителен
    assert client.get("/auth/me").status_code == 401


def test_logout_without_csrf_rejected(client, env):
    login(client)
    assert client.post("/auth/logout").status_code == 403
    assert not only_session(env[1])["revoked"]


def test_mutating_route_requires_csrf(client):
    token = login(client).json()["csrf_token"]
    assert client.post("/mutate").status_code == 403
    assert client.post("/mutate", headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert client.post("/mutate", headers={"X-CSRF-Token": token}).status_code == 200


def test_csrf_token_is_bound_to_session(client):
    token_a = login(client).json()["csrf_token"]
    other = TestClient(client.app)
    login(other, "boss")
    assert other.post("/mutate", headers={"X-CSRF-Token": token_a}).status_code == 403


def test_mutating_route_without_session_401(client):
    assert client.post("/mutate", headers={"X-CSRF-Token": "x"}).status_code == 401


def test_require_admin(client):
    token = login(client, "ivan").json()["csrf_token"]
    assert client.post("/admin-only", headers={"X-CSRF-Token": token}).status_code == 403
    boss = TestClient(client.app)
    t = login(boss, "boss").json()["csrf_token"]
    assert boss.post("/admin-only", headers={"X-CSRF-Token": t}).status_code == 200


def test_after_login_hook_gets_password_and_failure_does_not_break_login(env, caplog):
    app = env[0]
    seen = []
    app.state.after_login_hooks = [lambda lg, pw: 1 / 0, lambda lg, pw: seen.append((lg, pw))]
    with caplog.at_level(logging.DEBUG):
        r = login(TestClient(app))
    assert r.status_code == 200
    assert seen == [("ivan", PASSWORD)]
    assert PASSWORD not in caplog.text


def test_password_not_in_response_logs_or_session_store(client, env, caplog):
    with caplog.at_level(logging.DEBUG):
        ok = login(client)
        bad = login(client, "ivan", PASSWORD + "x")
        env[2].down = True
        down = login(client)
    for r in (ok, bad, down):
        assert PASSWORD not in r.text and PASSWORD not in json.dumps(dict(r.headers))
    assert PASSWORD not in caplog.text
    assert PASSWORD not in repr(env[1].sessions)


def test_json_log_filter_masks_password_in_payload(capsys):
    configure_logging([])
    logging.getLogger("t").error('body {"login": "ivan", "password": "%s"}', PASSWORD)
    out = capsys.readouterr()
    assert PASSWORD not in out.out + out.err


# --- адаптер LDAP: фильтр и защита от пустого пароля (без сети) ---


def test_filter_escapes_injection():
    f = build_filter("(uid={login})", "a)(uid=*))(|(uid=*")
    assert f == "(uid=a\\29\\28uid=\\2a\\29\\29\\28|\\28uid=\\2a)"


def test_adapter_requires_placeholder():
    with pytest.raises(ConfigError):
        Ldap3Adapter("ldap://h", "dc=x", "(uid=fixed)")
    with pytest.raises(ConfigError):
        Ldap3Adapter("", "dc=x", "(uid={login})")


@pytest.mark.parametrize(
    "login_,pw", [("ivan", ""), ("", "x"), ("iv\x00an", "x"), ("ivan", "a\x00")]
)
def test_adapter_rejects_empty_and_nul_without_network(login_, pw):
    ad = Ldap3Adapter("ldap://127.0.0.1:1", "dc=x", "(uid={login})")
    assert ad.authenticate(login_, pw) is False


def test_adapter_unreachable_server_is_unavailable_without_details():
    ad = Ldap3Adapter("ldap://127.0.0.1:1", "dc=x", "(uid={login})", timeout=1)
    with pytest.raises(LdapUnavailable) as e:
        ad.authenticate("ivan", PASSWORD)
    assert PASSWORD not in str(e.value) and "127.0.0.1" not in str(e.value)


def test_openapi_file_is_current_and_documents_csrf():
    from app.export_openapi import PATH, render

    assert PATH.read_text(encoding="utf-8") == render()
    spec = json.loads(render())
    assert "CsrfToken" in spec["components"]["securitySchemes"]
    assert "/auth/login" in spec["paths"] and "/auth/me" in spec["paths"]
