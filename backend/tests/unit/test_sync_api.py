"""API синхронизации (KON-35): права, «Обновить», отказы Jira как есть, эпик. БД не нужна."""
from __future__ import annotations

import base64
import os
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import AppUser
from app.auth.sessions import csrf_token, hash_session_id, new_session_id
from app.config import Secret, Settings
from app.integrations.jira import (
    CODE_JIRA_ERROR,
    CODE_JIRA_UNAVAILABLE,
    CODE_NEEDS_REAUTHORIZATION,
    CODE_NO_PERMISSION,
    JiraIntegrationError,
)
from app.jira_session.service import JiraStatusInfo
from app.sync import SyncService, SyncSettings
from app.sync.router import router as sync_router

from ._sync_fakes import FIXED_NOW, T0, FakeJira, FakeStore, FakeSyncRepo, issue

KEY = base64.b64encode(os.urandom(32)).decode()
JIRA_TEXT = "Для этой операции у вас нет прав в проекте ANALYTICS"


class AuthRepo:
    def __init__(self) -> None:
        self.sessions: dict[bytes, str] = {}

    def get_user(self, login):
        return AppUser(login, "user", True)

    def get_active_session(self, h):
        return (self.sessions[h], T0) if h in self.sessions else None

    def touch_session(self, h): ...

    def revoke_session(self, h): ...


class JiraSessionStub:
    def __init__(self) -> None:
        self.info = JiraStatusInfo("ok")

    def status(self, login):
        return self.info


@pytest.fixture()
def env():
    FIXED_NOW[0] = T0
    repo, jira = FakeSyncRepo(), FakeJira()
    clock = {"now": T0}
    service = SyncService(repo, jira, FakeStore({"ivan": "FAKE_sealed_ivan"}),
                          SyncSettings(jira_base_url="https://jira.example.internal"),
                          now=lambda: clock["now"])
    app = FastAPI()
    app.include_router(sync_router)
    auth = AuthRepo()
    app.state.settings = Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(KEY))
    app.state.repo = auth
    app.state.sync = service
    app.state.jira_session = JiraSessionStub()
    sid = new_session_id()
    auth.sessions[hash_session_id(sid)] = "ivan"
    client = TestClient(app)
    client.cookies.set("re_session", sid)
    headers = {"X-CSRF-Token": csrf_token(app.state.settings.enc_key_bytes(), sid)}
    return client, headers, repo, jira, app, clock


def test_endpoints_require_login():
    app = FastAPI()
    app.include_router(sync_router)
    app.state.repo = AuthRepo()
    c = TestClient(app)
    for method, url in (("get", "/sync/needs"), ("get", "/sync/employees"),
                        ("get", "/sync/status"), ("post", "/sync/refresh"),
                        ("get", "/sync/epics/SA-1")):
        assert getattr(c, method)(url).status_code == 401


def test_refresh_requires_csrf(env):
    client, headers, *_ = env
    assert client.post("/sync/refresh", json={"scope": "needs"}).status_code == 403


def test_refresh_ok_returns_stamp(env):
    client, headers, repo, jira, *_ = env
    repo.set_poller("ivan")
    jira.issues = [issue("ANALYTICS-1"), issue("ANALYTICS-2")]
    r = client.post("/sync/refresh", json={"scope": "needs"}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["upserted"] == 2 and body["sync"]["state"] == "ok"
    assert body["sync"]["last_success_at"] is not None
    assert set(repo.needs) == {"ANALYTICS-1", "ANALYTICS-2"}


def test_refresh_unknown_scope_is_422(env):
    client, headers, *_ = env
    assert client.post("/sync/refresh", json={"scope": "x"}, headers=headers).status_code == 422


@pytest.mark.parametrize(("code", "http", "state"), [
    (CODE_NEEDS_REAUTHORIZATION, 409, "needs_reauthorization"),
    (CODE_NO_PERMISSION, 403, "no_permission"),
    (CODE_JIRA_UNAVAILABLE, 503, "unavailable"),
    (CODE_JIRA_ERROR, 502, "blocked"),
])
def test_refresh_failures_are_distinguished_and_text_is_as_is(env, code, http, state):
    client, headers, repo, jira, *_ = env
    jira.error = JiraIntegrationError(JIRA_TEXT, code=code)
    r = client.post("/sync/refresh", json={"scope": "needs"}, headers=headers)
    assert r.status_code == http  # 401 приложения не используется
    d = r.json()["detail"]
    assert d["state"] == state and d["message"] == JIRA_TEXT


def test_refresh_blocked_when_user_jira_session_is_not_ok(env):
    client, headers, repo, jira, app, _ = env
    app.state.jira_session.info = JiraStatusInfo("blocked", "no_permission", JIRA_TEXT)
    r = client.post("/sync/refresh", json={"scope": "needs"}, headers=headers)
    assert r.status_code == 409 and r.json()["detail"]["message"] == JIRA_TEXT
    assert jira.calls == []


def test_refresh_when_already_running_reports_busy(env):
    client, headers, repo, *_ = env
    repo.busy.add("needs")
    r = client.post("/sync/refresh", json={"scope": "needs"}, headers=headers)
    assert r.status_code == 200 and r.json()["busy"] is True


def test_sync_status_shows_paused_without_active_users(env):
    client, *_ = env
    stamps = {s["scope"]: s for s in client.get("/sync/status").json()}
    assert stamps["needs"]["state"] == "paused" and stamps["needs"]["stale"] is True
    assert stamps["needs"]["paused"] is True and stamps["needs"]["interval_sec"] == 300


def test_sync_status_shows_reauth_state_with_jira_text(env):
    client, headers, repo, jira, *_ = env
    repo.set_poller("ivan")
    jira.error = JiraIntegrationError("Сессия истекла", code=CODE_NEEDS_REAUTHORIZATION)
    client.post("/sync/refresh", json={"scope": "needs"}, headers=headers)
    needs = client.get("/sync/status").json()[0]
    assert needs["state"] == "needs_reauthorization" and needs["message"] == "Сессия истекла"


def test_epic_endpoint_returns_rules_and_marks_stale_on_refusal(env):
    client, headers, repo, jira, app, clock = env
    r = client.get("/sync/epics/SA-10")
    assert r.status_code == 200
    assert r.json()["rules"][0]["name"] == "NDA" and r.json()["stale"] is False
    clock["now"] = T0 + timedelta(hours=3)
    jira.error = JiraIntegrationError("Jira не отвечает", code=CODE_JIRA_UNAVAILABLE)
    r = client.get("/sync/epics/SA-10")
    assert r.status_code == 200
    body = r.json()
    assert body["stale"] is True and body["error"]["message"] == "Jira не отвечает"
    assert body["error"]["state"] == "unavailable" and body["rules"]


def test_epic_endpoint_error_without_cache_and_bad_key(env):
    client, headers, repo, jira, *_ = env
    jira.error = JiraIntegrationError(JIRA_TEXT, code=CODE_NO_PERMISSION)
    r = client.get("/sync/epics/SA-10")
    assert r.status_code == 403 and r.json()["detail"]["message"] == JIRA_TEXT
    assert client.get("/sync/epics/not-a-key").status_code == 422


def test_employees_503_when_cv_pool_missing():
    FIXED_NOW[0] = T0
    repo = FakeSyncRepo(with_cv=False)
    service = SyncService(repo, FakeJira(), FakeStore({}), SyncSettings())
    app = FastAPI()
    app.include_router(sync_router)
    auth = AuthRepo()
    sid = new_session_id()
    auth.sessions[hash_session_id(sid)] = "ivan"
    app.state.repo, app.state.sync = auth, service
    c = TestClient(app)
    c.cookies.set("re_session", sid)
    assert c.get("/sync/employees").status_code == 503
