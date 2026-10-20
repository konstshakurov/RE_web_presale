"""Ориентир ставки (KON-95, FR-019, BR-009): параметры, ответ, настройка НДС. БД не нужна."""
from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import AppUser
from app.auth.sessions import hash_session_id, new_session_id
from app.config import Secret, Settings
from app.sync.rate_benchmark import DEFAULT_VAT_PERCENT, months_ago, parse_vat
from app.sync.rate_benchmark import router as rate_router

from ._sync_fakes import T0

KEY = base64.b64encode(os.urandom(32)).decode()


class AuthRepo:
    def __init__(self) -> None:
        self.sessions: dict[bytes, str] = {}

    def get_user(self, login):
        return AppUser(login, "user", True)

    def get_active_session(self, h):
        return (self.sessions[h], T0) if h in self.sessions else None

    def touch_session(self, h): ...

    def revoke_session(self, h): ...


class Repo:
    def __init__(self) -> None:
        self.setting: str | None = None
        self.calls: list[tuple[datetime, float, int]] = []
        self.groups: list[dict] = []

    def get_setting(self, key):
        assert key == "vat_percent"
        return self.setting

    def rate_benchmark(self, since, vat_percent, min_count):
        self.calls.append((since, vat_percent, min_count))
        return self.groups


class Sync:
    def __init__(self, repo) -> None:
        self.repo = repo


@pytest.fixture()
def env():
    app = FastAPI()
    app.include_router(rate_router)
    repo = Repo()
    app.state.sync = Sync(repo)
    app.state.settings = Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(KEY))
    auth = AuthRepo()
    app.state.repo = auth
    sid = new_session_id()
    auth.sessions[hash_session_id(sid)] = "ivan"
    client = TestClient(app)
    client.cookies.set("re_session", sid)
    return client, repo, app


def test_requires_login(env):
    _, _, app = env
    assert TestClient(app).get("/needs/rate-benchmark").status_code == 401


def test_defaults_to_six_months_and_vat_22(env):
    client, repo, _ = env
    repo.groups = [
        {"currency": "RUB", "unit": "ч", "count": 4, "mean": 100.0, "median": 90.5},
        {"currency": "USD", "unit": None, "count": 2, "mean": None, "median": None},
    ]
    r = client.get("/needs/rate-benchmark")
    assert r.status_code == 200
    body = r.json()
    assert body["period_months"] == 6 and body["vat_percent"] == 22.0
    assert body["groups"][0] == {
        "currency": "RUB", "unit": "ч", "count": 4, "mean": 100.0, "median": 90.5}
    assert body["groups"][1]["mean"] is None and body["groups"][1]["median"] is None
    assert body["as_of"]
    since, vat, min_count = repo.calls[0]
    assert vat == 22.0 and min_count == 3
    assert (datetime.now(UTC) - since).days in range(180, 187)


def test_empty_period_gives_empty_groups(env):
    client, _, _ = env
    r = client.get("/needs/rate-benchmark?period_months=12")
    assert r.status_code == 200 and r.json()["groups"] == [] and r.json()["period_months"] == 12


@pytest.mark.parametrize("value", ["0", "-1", "61", "abc", "1.5", ""])
def test_bad_period_is_422(env, value):
    client, repo, _ = env
    assert client.get(f"/needs/rate-benchmark?period_months={value}").status_code == 422
    assert repo.calls == []


def test_vat_setting_is_used(env):
    client, repo, _ = env
    repo.setting = "20"
    assert client.get("/needs/rate-benchmark").json()["vat_percent"] == 20.0
    assert repo.calls[0][1] == 20.0


@pytest.mark.parametrize(("raw", "want"), [
    (None, DEFAULT_VAT_PERCENT), ("", DEFAULT_VAT_PERCENT), ("abc", DEFAULT_VAT_PERCENT),
    ("-1", DEFAULT_VAT_PERCENT), ("101", DEFAULT_VAT_PERCENT), ("22", 22.0),
    ("20,5", 20.5), (" 0 ", 0.0),
])
def test_parse_vat(raw, want):
    assert parse_vat(raw) == want


def test_months_ago_calendar():
    t = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    assert months_ago(t, 6) == datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
    assert months_ago(t, 10) == datetime(2025, 12, 20, 12, 0, tzinfo=UTC)
    assert months_ago(t, 12) == datetime(2025, 10, 20, 12, 0, tzinfo=UTC)
    assert months_ago(datetime(2026, 8, 31, tzinfo=UTC), 6) == datetime(2026, 2, 28, tzinfo=UTC)
    assert months_ago(datetime(2026, 3, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
