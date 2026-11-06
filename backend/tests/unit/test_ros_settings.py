"""API параметров ROS (FR-041, Dev-53): чтение всем, запись только админ, CSRF, границы."""
from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import router
from app.auth.sessions import csrf_token, hash_session_id, new_session_id
from app.config import Secret, Settings
from app.ros import StaticRosSettings
from app.ros.router import router as ros_router

KEY = base64.b64encode(os.urandom(32)).decode()
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _User:
    def __init__(self, login, role, active, display_name=None):
        self.ldap_login, self.role, self.active = login, role, active
        self.display_name = display_name


class _Detail:
    def __init__(self, login, role, active):
        self.ldap_login, self.role, self.active = login, role, active
        self.created_at = self.updated_at = NOW


class FakeRepo:
    """Минимум для аутентификации и проверки роли (как в test_admin_users)."""

    def __init__(self) -> None:
        self.users: dict[str, _Detail] = {"boss": _Detail("boss", "admin", True),
                                          "ivan": _Detail("ivan", "user", True)}
        self.sessions: dict[bytes, dict] = {}

    def get_user(self, login):
        u = self.users.get(login)
        return _User(u.ldap_login, u.role, u.active) if u else None

    def get_active_session(self, h):
        s = self.sessions.get(h)
        return None if not s or s["revoked"] else (s["login"], NOW)

    def touch_session(self, h): ...

    def revoke_session(self, h):
        self.sessions[h]["revoked"] = True


def env():
    app = FastAPI()
    app.include_router(router)
    app.include_router(ros_router)
    app.state.settings = Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(KEY))
    app.state.repo = FakeRepo()
    app.state.ros = StaticRosSettings()
    return app


def session_for(app, login):
    sid = new_session_id()
    app.state.repo.sessions[hash_session_id(sid)] = {"login": login, "revoked": False}
    c = TestClient(app)
    c.cookies.set("re_session", sid)
    tok = csrf_token(app.state.settings.enc_key_bytes(), sid)
    return c, {"X-CSRF-Token": tok}


def test_anonymous_get_401():
    assert TestClient(env()).get("/admin/ros/settings").status_code == 401


def test_get_defaults_any_user():
    app = env()
    c, _ = session_for(app, "ivan")  # не админ: чтение доступно (расчёт на клиенте)
    r = c.get("/admin/ros/settings")
    assert r.status_code == 200
    assert r.json() == {
        "billable_hours": 165.0, "indirect_cost": 53000.0, "profit_tax_rate": 5.0,
        "vat_rate": 22.0, "insurance_before": 15.2, "insurance_after": 7.8,
        "fot_limit": 2979000.0,
    }


def test_put_user_403_and_csrf_required():
    app = env()
    ivan, _ = session_for(app, "ivan")
    body = StaticRosSettings().all()
    assert ivan.put("/admin/ros/settings", json=body).status_code == 403
    boss, _ = session_for(app, "boss")
    assert boss.put("/admin/ros/settings", json=body).status_code == 403  # без CSRF


def test_put_admin_saves_and_read_back():
    app = env()
    boss, h = session_for(app, "boss")
    body = {**StaticRosSettings().all(), "vat_rate": 20.0}
    r = boss.put("/admin/ros/settings", json=body, headers=h)
    assert r.status_code == 200 and r.json()["vat_rate"] == 20.0
    # общие: чтение тем же пользователем возвращает сохранённое
    assert boss.get("/admin/ros/settings").json()["vat_rate"] == 20.0


def test_put_rejects_value_out_of_bounds():
    app = env()
    boss, h = session_for(app, "boss")
    body = {**StaticRosSettings().all(), "vat_rate": 200.0}
    r = boss.put("/admin/ros/settings", json=body, headers=h)
    assert r.status_code == 400 and "vat_rate" in r.json()["detail"]
    assert boss.get("/admin/ros/settings").json()["vat_rate"] == 22.0  # не изменилось


def test_put_requires_all_fields():
    app = env()
    boss, h = session_for(app, "boss")
    r = boss.put("/admin/ros/settings", json={"vat_rate": 20.0}, headers=h)
    assert r.status_code == 422


def test_static_settings_validates():
    with pytest.raises(ValueError, match="insurance_before"):
        StaticRosSettings({"insurance_before": -1.0})
    assert StaticRosSettings({"insurance_before": 0.0}).all()["insurance_before"] == 0.0
    s = StaticRosSettings()
    s.set({"vat_rate": 0.0})
    assert s.all()["vat_rate"] == 0.0
