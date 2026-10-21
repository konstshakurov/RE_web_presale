"""QA-4 (KON-56): сквозные проверки входа на уровне HTTP API с подменой LDAP и БД.

Ссылки TC-* ведут в qa/auth_cases.md. Тесты не повторяют unit-набор Dev (backend/tests/unit):
здесь сценарии с несколькими шагами и несколькими пользователями.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ._support import (
    DENIED_TEXT,
    FAKE_PASSWORD,
    FakeLdap,
    MemRepo,
    build_app,
    csrf_headers,
    login,
    session_hash,
)

ACCOUNTS = {"boss": FAKE_PASSWORD, "ivan": FAKE_PASSWORD, "outsider": FAKE_PASSWORD,
            "second": FAKE_PASSWORD}


@pytest.fixture()
def env():
    repo = MemRepo()
    repo.put("boss", "admin")
    repo.put("ivan", "user")
    ldap = FakeLdap(dict(ACCOUNTS))
    app, _ = build_app(ldap, repo)
    return app, repo, ldap


def client_for(app) -> TestClient:
    return TestClient(app)


def section_keys(c) -> list[str]:
    return [s["key"] for s in c.get("/auth/me").json()["sections"]]


# --- TC-AUTH-01/02/04/05: основной вход, список допущенных (FR-024, FR-021) ---

def test_tc01_valid_ldap_in_list_gets_session_and_role_sections(env):
    app, repo, _ = env
    c = client_for(app)
    r = login(c, "ivan")
    assert r.status_code == 200 and r.json()["role"] == "user"
    assert "re_session" in c.cookies
    assert len(repo.sessions) == 1


def test_tc02_valid_ldap_not_in_list_denied_no_session_and_same_text(env):
    app, repo, _ = env
    c = client_for(app)
    r_out = login(c, "outsider")
    r_bad = login(c, "ivan", "FAKE_wrong")
    assert r_out.status_code == r_bad.status_code == 401
    assert r_out.json() == r_bad.json() == {"detail": DENIED_TEXT}  # причина не раскрывается
    assert not repo.sessions and "re_session" not in c.cookies


@pytest.mark.parametrize("pw", [" ", "\x00"])
def test_tc04_empty_like_password_never_creates_session(env, pw):
    app, repo, _ = env
    r = login(client_for(app), "ivan", pw)
    assert r.status_code == 401 and not repo.sessions


@pytest.mark.parametrize("name", [
    "*", "*)(uid=*", "ivan)(|(uid=*", "ivan\\00", "ivan,cn=users",
])
def test_tc05_injection_like_logins_are_not_accepted(env, name):
    app, repo, _ = env
    r = login(client_for(app), name)
    assert r.status_code == 401 and not repo.sessions


def _assert_schema_rejection(r, repo, ldap, secret=None):
    # FR-024/NFR-006 не требуют именно 401 для пустого ввода: пустой логин/пароль - ошибка
    # формата запроса (422, штатная валидация схемы), LDAP при этом не вызывается и сессия
    # не создаётся. Раскрытия причины отказа аутентификации (неверный пароль / нет в списке)
    # в ответе нет. KON-110.
    assert r.status_code == 422 and not repo.sessions and ldap.calls == []
    assert DENIED_TEXT not in r.text and "re_session" not in r.cookies
    if secret:
        assert secret not in r.text


def test_tc04_empty_password_is_schema_error_without_ldap_call(env):
    app, repo, ldap = env
    r = login(client_for(app), "ivan", "")
    _assert_schema_rejection(r, repo, ldap)


@pytest.mark.parametrize("name", ["", "  "])
def test_tc05_blank_login_is_schema_error_without_ldap_call(env, name):
    app, repo, ldap = env
    r = login(client_for(app), name)
    _assert_schema_rejection(r, repo, ldap, FAKE_PASSWORD)


def test_tc05b_login_is_normalized_but_not_wildcarded(env):
    app, _, _ = env
    assert login(client_for(app), "  IVAN ").status_code == 200  # lower + trim
    assert login(client_for(app), "iv*").status_code == 401


def test_login_overlong_fields_rejected_by_validation(env):
    app, _, _ = env
    r = client_for(app).post("/auth/login", json={"login": "a" * 129, "password": FAKE_PASSWORD})
    assert r.status_code == 422
    r = client_for(app).post("/auth/login", json={"login": "ivan", "password": "p" * 1025})
    assert r.status_code == 422


def test_login_malformed_body_does_not_leak_password(env, caplog):
    app, _, _ = env
    caplog.set_level(logging.DEBUG)
    r = client_for(app).post("/auth/login", json={"login": 5, "password": FAKE_PASSWORD})
    assert r.status_code == 422
    assert FAKE_PASSWORD not in r.text
    assert FAKE_PASSWORD not in caplog.text


# --- TC-AUTH-06: отзыв при открытой сессии (UC-006 1а, FR-021) ---

def test_tc06_revoked_user_open_session_dies_on_next_request_and_relogin_denied(env):
    app, _, _ = env
    user, admin = client_for(app), client_for(app)
    assert login(user, "ivan").status_code == 200
    assert login(admin, "boss").status_code == 200
    assert user.get("/auth/me").status_code == 200
    r = admin.patch("/admin/users/ivan", json={"active": False}, headers=csrf_headers(admin))
    assert r.status_code == 200
    assert user.get("/auth/me").status_code == 401
    assert login(client_for(app), "ivan").status_code == 401  # LDAP верен, но не допущен
    admin.post("/admin/users", json={"login": "IVAN"}, headers=csrf_headers(admin))
    assert login(client_for(app), "ivan").status_code == 200  # доступ возвращён


def test_tc06b_revoke_kills_all_sessions_of_user_not_of_others(env):
    app, _, _ = env
    a1, a2, other, admin = (client_for(app) for _ in range(4))
    for c, n in ((a1, "ivan"), (a2, "ivan"), (other, "boss"), (admin, "boss")):
        assert login(c, n).status_code == 200
    admin.patch("/admin/users/ivan", json={"active": False}, headers=csrf_headers(admin))
    assert a1.get("/auth/me").status_code == a2.get("/auth/me").status_code == 401
    assert other.get("/auth/me").status_code == 200


# --- TC-AUTH-07/08: logout, TTL (FR-024, ADR-008) ---

def test_tc07_logout_invalidates_server_side_even_if_cookie_is_replayed(env):
    app, _, _ = env
    c = client_for(app)
    login(c, "ivan")
    sid = c.cookies.get("re_session")
    assert c.post("/auth/logout", headers=csrf_headers(c)).status_code == 204
    replay = client_for(app)
    replay.cookies.set("re_session", sid)
    assert replay.get("/auth/me").status_code == 401


def test_tc08_session_expires_by_ttl_and_cookie_max_age_matches():
    app, repo = build_app(FakeLdap(dict(ACCOUNTS)), ttl_hours=2)
    repo.put("ivan")
    c = client_for(app)
    r = login(c, "ivan")
    assert "Max-Age=7200" in r.headers["set-cookie"]
    repo.sessions[session_hash(c)]["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    assert c.get("/auth/me").status_code == 401


def test_cookie_flags_httponly_samesite():
    app, repo = build_app(FakeLdap(dict(ACCOUNTS)))
    repo.put("ivan")
    ck = login(client_for(app), "ivan").headers["set-cookie"].lower()
    assert "httponly" in ck and "samesite=lax" in ck and "path=/" in ck


def test_session_id_in_store_is_hash_not_cookie_value(env):
    app, repo, _ = env
    c = client_for(app)
    login(c, "ivan")
    sid = c.cookies.get("re_session")
    assert all(sid.encode() != k and sid not in repr(v) for k, v in repo.sessions.items())


def test_distinct_logins_give_distinct_session_ids(env):
    app, _, _ = env
    ids = set()
    for _ in range(3):
        c = client_for(app)
        login(c, "ivan")
        ids.add(c.cookies.get("re_session"))
    assert len(ids) == 3


# --- TC-AUTH-09/10: права на /admin/* и видимость «Настройки» (FR-031, FR-038) ---

def test_tc09_non_admin_forbidden_on_all_admin_routes_even_with_valid_csrf(env):
    app, _, _ = env
    c = client_for(app)
    login(c, "ivan")
    h = csrf_headers(c)
    assert c.get("/admin/users").status_code == 403
    assert c.post("/admin/users", json={"login": "x"}, headers=h).status_code == 403
    assert c.patch("/admin/users/boss", json={"active": False}, headers=h).status_code == 403
    assert c.post("/admin-probe", headers=h).status_code == 403


def test_tc09b_anonymous_gets_401_not_403(env):
    app, _, _ = env
    c = client_for(app)
    assert c.get("/admin/users").status_code == 401
    assert c.post("/admin/users", json={"login": "x"}).status_code == 401


def test_tc09c_role_comes_from_store_not_from_client(env):
    app, _, _ = env
    r = client_for(app).post(
        "/auth/login", json={"login": "ivan", "password": FAKE_PASSWORD, "role": "admin"}
    )
    assert r.status_code == 200 and r.json()["role"] == "user"


def test_tc10_settings_visible_only_for_admin(env):
    app, _, _ = env
    u, a = client_for(app), client_for(app)
    login(u, "ivan")
    login(a, "boss")
    assert section_keys(u) == ["needs", "employees", "resume"]
    assert section_keys(a) == ["needs", "employees", "resume", "settings"]


def test_tc10b_demoted_admin_loses_settings_on_next_request(env):
    app, repo, _ = env
    repo.put("second", "admin")
    a, b = client_for(app), client_for(app)
    login(a, "boss")
    login(b, "second")
    a.patch("/admin/users/second", json={"role": "user"}, headers=csrf_headers(a))
    assert "settings" not in section_keys(b)
    assert b.get("/admin/users").status_code == 403


# --- TC-AUTH-11: последний админ (UC-006, FR-021) ---

def test_tc11_last_admin_cannot_be_revoked_or_demoted(env):
    app, repo, _ = env
    a = client_for(app)
    login(a, "boss")
    h = csrf_headers(a)
    assert a.patch("/admin/users/boss", json={"active": False}, headers=h).status_code == 409
    assert a.patch("/admin/users/boss", json={"role": "user"}, headers=h).status_code == 409
    assert a.patch("/admin/users/boss", json={"active": False, "role": "user"},
                   headers=h).status_code == 409
    assert repo.users["boss"].active and repo.users["boss"].role == "admin"
    assert a.get("/auth/me").status_code == 200  # сессия цела


def test_tc11b_two_admins_one_removable_then_other_protected(env):
    app, repo, _ = env
    repo.put("second", "admin")
    a = client_for(app)
    login(a, "boss")
    assert a.patch("/admin/users/second", json={"active": False},
                   headers=csrf_headers(a)).status_code == 200
    assert a.patch("/admin/users/boss", json={"active": False},
                   headers=csrf_headers(a)).status_code == 409


def test_tc11c_inactive_admin_does_not_count(env):
    app, repo, _ = env
    repo.put("second", "admin", active=False)
    a = client_for(app)
    login(a, "boss")
    assert a.patch("/admin/users/boss", json={"role": "user"},
                   headers=csrf_headers(a)).status_code == 409


def test_tc11d_unknown_login_404_and_no_delete_route(env):
    app, _, _ = env
    a = client_for(app)
    login(a, "boss")
    assert a.patch("/admin/users/ghost", json={"active": False},
                   headers=csrf_headers(a)).status_code == 404
    assert a.delete("/admin/users/ivan", headers=csrf_headers(a)).status_code in (404, 405)


# --- TC-AUTH-12/13: LDAP недоступен; Jira (FR-029) ---

def test_tc12_ldap_down_503_no_session_no_details_then_recovers(env):
    app, repo, ldap = env
    ldap.down = True
    r = login(client_for(app), "ivan")
    assert r.status_code == 503 and "down" not in r.text and not repo.sessions
    ldap.down = False
    assert login(client_for(app), "ivan").status_code == 200


def test_tc12b_existing_session_survives_ldap_outage(env):
    app, _, ldap = env
    c = client_for(app)
    login(c, "ivan")
    ldap.down = True
    assert c.get("/auth/me").status_code == 200  # LDAP нужен только на входе


def test_tc13_jira_failure_in_after_login_hook_does_not_block_login(env):
    app, _, _ = env
    calls = []

    def hook(login_, password):
        calls.append(login_)
        raise RuntimeError("jira down")

    app.state.after_login_hooks = [hook]
    r = login(client_for(app), "ivan")
    assert r.status_code == 200 and calls == ["ivan"]
    assert FAKE_PASSWORD not in r.text


def test_tc13b_jira_status_reported_as_is(env):
    app, _, _ = env

    class Info:
        state, code, message = "blocked", "login_blocked", "CAPTCHA required (текст Jira)"

    class Svc:
        def status(self, login_):
            return Info()

    app.state.jira_session = Svc()
    body = login(client_for(app), "ivan").json()
    assert body["jira_status"] == {"state": "blocked", "code": "login_blocked",
                                   "message": "CAPTCHA required (текст Jira)"}


# --- TC-AUTH-14: CSRF (ADR-008) ---

def test_tc14_csrf_required_on_mutations_and_bound_to_session(env):
    app, _, _ = env
    a, b = client_for(app), client_for(app)
    login(a, "boss")
    login(b, "ivan")
    tok_a, tok_b = csrf_headers(a), csrf_headers(b)
    assert tok_a != tok_b
    assert a.post("/admin/users", json={"login": "n1"}).status_code == 403
    assert a.post("/admin/users", json={"login": "n1"},
                  headers={"X-CSRF-Token": ""}).status_code == 403
    assert a.post("/admin/users", json={"login": "n1"}, headers=tok_b).status_code == 403
    assert a.patch("/admin/users/ivan", json={"active": False}).status_code == 403
    assert a.post("/auth/logout").status_code == 403
    assert a.post("/admin/users", json={"login": "n1"}, headers=tok_a).status_code == 201
    assert a.get("/auth/me").status_code == 200  # безопасные методы без токена


def test_tc14b_csrf_stable_per_session_and_differs_between_sessions(env):
    app, _, _ = env
    c = client_for(app)
    login(c, "ivan")
    t1 = csrf_headers(c)
    assert csrf_headers(c) == t1
    c2 = client_for(app)
    login(c2, "ivan")
    assert csrf_headers(c2) != t1


# --- NFR-006: пароль не попадает в ответы и логи на пути входа ---

def test_nfr006_password_absent_from_responses_and_logs(env, caplog):
    app, repo, _ = env
    caplog.set_level(logging.DEBUG)
    c = client_for(app)
    texts = [login(c, "ivan").text, login(c, "ivan", "FAKE_wrong_pw_2").text,
             login(c, "outsider").text, c.get("/auth/me").text]
    blob = "\n".join(texts) + caplog.text
    assert FAKE_PASSWORD not in blob and "FAKE_wrong_pw_2" not in blob
    assert FAKE_PASSWORD not in json.dumps([repr(v) for v in repo.sessions.values()])
