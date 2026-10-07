"""Сессия Jira при входе (KON-52): фейки порта и БД, сеть не нужна."""
from __future__ import annotations

import base64
import json
import logging

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import AppUser
from app.auth import SessionContext, router
from app.jira_session import (
    DisabledJiraSession,
    JiraClientSessionAdapter,
    JiraSessionError,
    JiraSessionService,
    require_jira_ok,
)
from app.jira_session.port import (
    CODE_AUTH_FAILED,
    CODE_JIRA_UNAVAILABLE,
    STATE_BLOCKED,
    STATE_UNAVAILABLE,
)
from app.secrets import SealedSecret, SecretStore
from tests.conftest import FAKE_RAW_TOKEN, error_response, session_response
from tests.unit.test_auth import KEY, PASSWORD, FakeLdap, FakeRepo, login, make_settings

TOKEN = "SEALED-JIRA-TOKEN-aZ09xyzQ-0001"
REFUSAL = "Jira: недостаточно прав для входа (текст отказа)"


class SecretRepo(FakeRepo):
    """FakeRepo + secret_store в памяти (то, что ушло бы в БД)."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, SealedSecret] = {}

    def upsert_jira_session_secret(self, owner, sealed):
        self.rows[owner] = sealed

    def get_jira_session_secret(self, owner):
        return self.rows.get(owner)

    def delete_jira_session_secret(self, owner):
        return self.rows.pop(owner, None) is not None


class FakePort:
    def __init__(self) -> None:
        self.token = TOKEN
        self.error: JiraSessionError | None = None

    def login(self, username, password):
        if self.error:
            raise self.error
        return self.token


def _store(repo):
    return SecretStore(repo, base64.b64decode(KEY), "k1")


@pytest.fixture()
def env():
    repo = SecretRepo()
    repo.users["ivan"] = AppUser("ivan", "user", True)
    port = FakePort()
    service = JiraSessionService(port, _store(repo))
    app = FastAPI()
    app.include_router(router)

    @app.post("/jira-action")
    def action(ctx: SessionContext = Depends(require_jira_ok)):
        return {"ok": ctx.login}

    app.state.settings = make_settings(session_cookie_secure=False)
    app.state.repo = repo
    app.state.ldap = FakeLdap({"ivan": PASSWORD})
    app.state.jira_session = service
    app.state.after_login_hooks = [service.after_login_hook]
    return app, repo, port, service


@pytest.fixture()
def client(env):
    return TestClient(env[0])


def hdr(me):
    return {"X-CSRF-Token": me["csrf_token"]}


def relogin(client, me):
    return client.post("/auth/jira-relogin", json={"password": PASSWORD}, headers=hdr(me))


def test_success_stores_one_encrypted_record(client, env):
    _, repo, _, service = env
    r = login(client)
    assert r.status_code == 200
    assert r.json()["jira_status"] == {"state": "ok", "code": "", "message": ""}
    assert list(repo.rows) == ["ivan"]
    row = repo.rows["ivan"]
    assert TOKEN.encode() not in row.ciphertext and TOKEN.encode() not in row.nonce
    assert service.get_user_jira_token("ivan") == TOKEN
    assert TOKEN not in r.text and PASSWORD not in r.text
    me = client.get("/auth/me")
    assert me.json()["jira_status"]["state"] == "ok"
    assert TOKEN not in me.text


def test_refusal_keeps_login_and_shows_text_as_is(client, env):
    _, repo, port, _ = env
    port.error = JiraSessionError(STATE_BLOCKED, CODE_AUTH_FAILED, REFUSAL)
    r = login(client)
    assert r.status_code == 200
    assert r.json()["jira_status"] == {
        "state": "blocked", "code": "auth_failed", "message": REFUSAL}
    assert client.get("/auth/me").json()["jira_status"]["message"] == REFUSAL
    assert repo.rows == {}


def test_unavailable_is_not_mixed_with_blocked(client, env):
    _, _, port, _ = env
    port.error = JiraSessionError(STATE_UNAVAILABLE, CODE_JIRA_UNAVAILABLE, "Jira не отвечает")
    r = login(client)
    st = r.json()["jira_status"]
    assert r.status_code == 200
    assert st["state"] == "unavailable" and st["code"] == "jira_unavailable"


def test_jira_actions_blocked_until_ok(client, env):
    _, _, port, _ = env
    port.error = JiraSessionError(STATE_BLOCKED, CODE_AUTH_FAILED, REFUSAL)
    me = login(client).json()
    r = client.post("/jira-action", headers=hdr(me))
    assert r.status_code == 409
    assert r.json()["detail"]["message"] == REFUSAL
    port.error = JiraSessionError(STATE_UNAVAILABLE, CODE_JIRA_UNAVAILABLE, "down")
    relogin(client, me)
    assert client.post("/jira-action", headers=hdr(me)).status_code == 503
    port.error = None
    relogin(client, me)
    assert client.post("/jira-action", headers=hdr(me)).status_code == 200


def test_relogin_replaces_token_no_duplicates(client, env):
    _, repo, port, service = env
    me = login(client).json()
    first = repo.rows["ivan"]
    port.token = "SEALED-JIRA-TOKEN-NEW-0002"
    r = relogin(client, me)
    assert r.status_code == 200 and r.json()["jira_status"]["state"] == "ok"
    assert list(repo.rows) == ["ivan"] and repo.rows["ivan"] != first
    assert service.get_user_jira_token("ivan") == "SEALED-JIRA-TOKEN-NEW-0002"
    login(client)  # повторный вход тоже заменяет
    assert list(repo.rows) == ["ivan"]


def test_relogin_requires_session_and_csrf(client):
    assert client.post("/auth/jira-relogin", json={"password": "x"}).status_code == 401
    login(client)
    assert client.post("/auth/jira-relogin", json={"password": "x"}).status_code == 403


def test_blocked_drops_token_unavailable_keeps_it(client, env):
    _, repo, port, _ = env
    me = login(client).json()
    port.error = JiraSessionError(STATE_UNAVAILABLE, CODE_JIRA_UNAVAILABLE, "down")
    relogin(client, me)
    assert "ivan" in repo.rows
    port.error = JiraSessionError(STATE_BLOCKED, CODE_AUTH_FAILED, REFUSAL)
    relogin(client, me)
    assert "ivan" not in repo.rows


def test_disabled_session_login_still_works(env):
    app, repo, _, _ = env
    app.state.jira_session = JiraSessionService(DisabledJiraSession(), _store(repo))
    app.state.after_login_hooks = [app.state.jira_session.after_login_hook]
    r = login(TestClient(app))
    st = r.json()["jira_status"]
    assert r.status_code == 200
    assert st["state"] == "unavailable" and st["code"] == "not_enabled"


def test_password_and_token_not_in_logs(client, env, caplog):
    caplog.set_level(logging.DEBUG)
    _, _, port, _ = env
    login(client)
    port.error = JiraSessionError(STATE_BLOCKED, CODE_AUTH_FAILED, REFUSAL)
    login(client)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert PASSWORD not in text and TOKEN not in text


def test_openapi_has_relogin_without_token(env):
    spec = json.dumps(env[0].openapi())
    assert "/auth/jira-relogin" in spec and "token" not in spec.replace("csrf_token", "")


# --- адаптер над настоящим JiraAdapter (MockTransport) ---

def test_real_adapter_success_returns_sealed_not_raw(adapter_factory):
    adapter = JiraClientSessionAdapter(adapter_factory(lambda req: session_response()))
    token = adapter.login("u", PASSWORD)
    assert token and FAKE_RAW_TOKEN not in token


def test_real_adapter_refusal_is_blocked_without_password(adapter_factory):
    def handler(req):
        return error_response(["x"], 401, {"x-seraph-loginreason": "AUTHENTICATED_FAILED"})

    adapter = JiraClientSessionAdapter(adapter_factory(handler))
    with pytest.raises(JiraSessionError) as e:
        adapter.login("u", PASSWORD)
    assert e.value.state == STATE_BLOCKED and e.value.code == CODE_AUTH_FAILED
    assert PASSWORD not in str(e.value) and PASSWORD not in repr(e.value.__dict__)
    assert e.value.__cause__ is None


def test_real_adapter_network_error_is_unavailable(adapter_factory):
    def handler(req):
        raise httpx.ConnectError("refused")

    adapter = JiraClientSessionAdapter(adapter_factory(handler))
    with pytest.raises(JiraSessionError) as e:
        adapter.login("u", PASSWORD)
    assert e.value.state == STATE_UNAVAILABLE and e.value.code == CODE_JIRA_UNAVAILABLE
