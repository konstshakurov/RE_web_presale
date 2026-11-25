"""API дублей потребностей (FR-014 v1.1, CR-023): вывод в списке, классификация, права. БД не нужна."""
from __future__ import annotations

import base64
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import AppUser
from app.auth.sessions import csrf_token, hash_session_id, new_session_id
from app.config import Secret, Settings
from app.jira_session.service import JiraStatusInfo
from app.sync import SyncService, SyncSettings
from app.sync.router import router as sync_router

from ._sync_fakes import FIXED_NOW, T0, FakeJira, FakeStore, FakeSyncRepo

KEY = base64.b64encode(os.urandom(32)).decode()

NEED_5965 = {
    "jira_key": "ANALYTICS-5965", "title": "Подбор аналитиков данных (Магнит)",
    "description": None, "requirements_text": None, "status": "Требуется кандидат",
    "epic_key": None, "epic_name": None, "employee_key": None, "assignee": None,
    "author": "mihail.ivanov", "contract_type": "Прямой контракт", "tags": [],
    "rate_amount": 320000.0, "rate_currency": "RUB", "rate_includes_vat": True,
    "rate_unit": "ч", "rate_proposer": "от производства",
    "jira_created_at": T0, "jira_updated_at": T0, "synced_at": T0,
}
DUP_5968 = {
    "jira_key": "ANALYTICS-5968", "title": "Субподряд «Открытые решения»",
    "match_percent": 52, "classification": "similar",
    "contract_type": "Субподрядчик", "rate_amount": 195000.0,
}


class AuthRepo:
    def __init__(self):
        self.sessions: dict[bytes, str] = {}

    def get_user(self, login):
        return AppUser(login, "user", True)

    def get_active_session(self, h):
        return (self.sessions[h], T0) if h in self.sessions else None

    def touch_session(self, h): ...

    def revoke_session(self, h): ...


class JiraSessionStub:
    def __init__(self):
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
    return client, headers, repo


def test_endpoints_require_login():
    app = FastAPI()
    app.include_router(sync_router)
    app.state.repo = AuthRepo()
    c = TestClient(app)
    assert c.get("/sync/needs").status_code == 401
    assert c.post("/sync/needs/ANALYTICS-5965/duplicates/ANALYTICS-5968",
                  json={"classification": "duplicate"}).status_code == 401


def test_needs_list_includes_duplicates(env):
    client, headers, repo = env
    repo.list_result = [NEED_5965]
    repo.duplicates = {"ANALYTICS-5965": [DUP_5968]}
    r = client.get("/sync/needs")
    assert r.status_code == 200
    item = r.json()["items"][0]
    d = item["duplicates"]
    assert len(d) == 1
    assert d[0]["jira_key"] == "ANALYTICS-5968"
    assert d[0]["match_percent"] == 52
    assert d[0]["classification"] == "similar"
    assert d[0]["rate_amount"] == 195000.0


def test_needs_list_without_duplicates_has_empty_field(env):
    client, headers, repo = env
    repo.list_result = [NEED_5965]
    r = client.get("/sync/needs")
    assert r.status_code == 200
    assert r.json()["items"][0]["duplicates"] == []


def test_classification_saved(env):
    client, headers, repo = env
    r = client.post("/sync/needs/ANALYTICS-5965/duplicates/ANALYTICS-5968",
                    json={"classification": "duplicate"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["classification"] == "duplicate"
    assert repo.classifications == [("ANALYTICS-5965", "ANALYTICS-5968", "duplicate")]


def test_classification_invalid_value_is_422(env):
    client, headers, repo = env
    r = client.post("/sync/needs/ANALYTICS-5965/duplicates/ANALYTICS-5968",
                    json={"classification": "copy"}, headers=headers)
    assert r.status_code == 422


def test_classification_requires_csrf(env):
    client, headers, repo = env
    r = client.post("/sync/needs/ANALYTICS-5965/duplicates/ANALYTICS-5968",
                    json={"classification": "duplicate"})
    assert r.status_code == 403


def test_sync_calls_duplicate_refresh(env):
    client, headers, repo = env
    repo.set_poller("ivan")
    r = client.post("/sync/refresh", json={"scope": "needs"}, headers=headers)
    assert r.status_code == 200
    assert repo.dup_calls == 1  # расчёт дублей выполняется при синхронизации (FR-014)
