"""QA-5 (KON-57): API входа и доступа против живого стенда (proxy -> api -> app_db, LDAP тестовый).

Кейсы - qa/auth_cases.md (TC-AUTH-*), требования FR-024, FR-021, UC-006, FR-031, FR-029.
Стенд: ``bash qa/stand/stand.sh up``. Каждый тест начинает с известного состояния списка
допущенных (фикстура ``baseline``): qa-admin - админ; qa-user, qa-revoked, qa-jirablock -
допущены; qa-outsider - не допущен.
"""
from __future__ import annotations

import subprocess
import threading
import time

import pytest

from ._stand import (
    COMPOSE_PROJECT,
    DENIED_TEXT,
    LDAP_DOWN_TEXT,
    PASSWORDS,
    Session,
    logged_in,
    requires_stand,
)

pytestmark = requires_stand

USER_NAMES = ("qa-user", "qa-revoked", "qa-jirablock")


@pytest.fixture()
def baseline():
    """Привести список допущенных к известному состоянию через API админа (FR-021)."""
    admin = logged_in("qa-admin")
    users = admin.users()
    if users["qa-admin"]["role"] != "admin" or not users["qa-admin"]["active"]:
        raise AssertionError("qa-admin должен быть активным админом (ADMIN_LOGINS стенда)")
    for extra in [u for u, d in users.items() if d["role"] == "admin" and u != "qa-admin" and d["active"]]:
        assert admin.patch(f"/admin/users/{extra}", json={"role": "user"}).status_code == 200
    for name in USER_NAMES:
        assert admin.post("/admin/users", json={"login": name}).status_code in (200, 201)
        assert admin.patch(f"/admin/users/{name}", json={"active": True, "role": "user"}).status_code == 200
    if "qa-outsider" in users and users["qa-outsider"]["active"]:
        assert admin.patch("/admin/users/qa-outsider", json={"active": False}).status_code == 200
    yield admin
    admin.close()


def _fresh_admin() -> Session:
    return logged_in("qa-admin")


# --- TC-AUTH-01: вход (FR-024, FR-021) -----------------------------------------------------------

def test_tc01_login_ok_cookie_and_profile(baseline):
    s = Session()
    r = s.login("qa-user")
    assert r.status_code == 200
    body = r.json()
    assert body["login"] == "qa-user" and body["role"] == "user"
    assert body["display_name"] == "QA User Fiction"          # cn из LDAP
    assert [x["key"] for x in body["sections"]] == ["needs", "employees", "resume"]
    assert PASSWORDS["qa-user"] not in r.text                   # пароль не в ответе
    cookie = r.headers["set-cookie"].lower()
    assert "re_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "max-age=28800" in cookie                            # SESSION_TTL_HOURS=8 (TC-AUTH-08)
    assert "path=/" in cookie
    assert s.me().status_code == 200


def test_tc01_me_requires_session():
    s = Session()
    assert s.me().status_code == 401
    assert Session().http.get("/auth/me", cookies={"re_session": "x" * 43}).status_code == 401


# --- TC-AUTH-02/03: отказы (FR-021, FR-024) ------------------------------------------------------

def test_tc02_outsider_denied_same_text_as_wrong_password(baseline):
    s = Session()
    r_out = s.login("qa-outsider")                              # верный LDAP, нет в списке
    r_bad = s.login("qa-user", "FAKE_wrong_password_1")        # неверный пароль
    r_unknown = s.login("qa-nonexistent-xyz", "FAKE_wrong_password_1")
    for r in (r_out, r_bad, r_unknown):
        assert r.status_code == 401
        assert r.json()["detail"] == DENIED_TEXT
        assert "set-cookie" not in r.headers                   # сессия не создана
    assert s.me().status_code == 401


@pytest.mark.parametrize("pw", ["", " ", "   ", "\x00", " ", "a​b", "пароль-кириллица"])
def test_tc04_empty_like_password_denied(baseline, pw):
    r = Session().login("qa-user", pw)
    # пустой пароль и NUL - ошибка формата (422, KON-76); остальные - отказ аутентификации (401)
    assert r.status_code == (422 if pw == "" or "\x00" in pw else 401) and "set-cookie" not in r.headers


# KON-67 исправлен (89b0cee): управляющий символ в пароле -> 401, не 503
@pytest.mark.parametrize("pw", ["\t", "a\tb", "\n", "\x01", "\x7f"])
def test_tc04_control_char_password_denied(baseline, pw):
    r = Session().login("qa-user", pw)
    assert r.status_code == 401 and "set-cookie" not in r.headers


@pytest.mark.parametrize(
    "name",
    ["*", "*)(uid=*", "qa-user)(|(uid=*", "qa-user,cn=x", "x+uid=qa-user", "\\", "qa-user\x00", "qa-*", "q*"],
)
def test_tc05_ldap_injection_denied(baseline, name):
    r = Session().login(name, PASSWORDS["qa-user"])
    # NUL в теле JSON - 422 на входе API (KON-76), остальные инъекции - 401
    assert r.status_code == (422 if "\x00" in name else 401), name
    assert "set-cookie" not in r.headers


@pytest.mark.parametrize("name", ["", "  ", "\t", "qa\nuser", "qa\x01user"])
def test_tc05d_blank_or_control_login_is_422(baseline, name):
    r = Session().login(name, PASSWORDS["qa-user"])
    assert r.status_code == 422 and "set-cookie" not in r.headers


def test_tc05b_login_normalized_trim_and_case(baseline):
    s = Session()
    r = s.login("  QA-USER ", PASSWORDS["qa-user"])
    assert r.status_code == 200 and r.json()["login"] == "qa-user"


def test_tc05c_field_limits(baseline):
    s = Session()
    long_pw = "FAKE_" + "p" * 1100
    assert s.login("qa-user", long_pw).status_code == 422
    assert s.http.post("/auth/login", json={"login": "x" * 129, "password": "a"}).status_code == 422
    assert s.http.post("/auth/login", json={"login": 1, "password": None}).status_code == 422
    assert s.http.post("/auth/login", json={}).status_code == 422
    assert s.http.post("/auth/login", content=b"not json",
                       headers={"content-type": "application/json"}).status_code == 422


# KON-68 исправлен (89b0cee): 422 не возвращает введённый пароль
def test_tc05c_overlong_password_not_echoed(baseline):
    long_pw = "FAKE_" + "p" * 1100
    r = Session().login("qa-user", long_pw)
    assert r.status_code == 422 and long_pw not in r.text
    r2 = Session().http.post("/auth/login", json={"login": "qa-user", "password": ["FAKE_secret_in_list"]})
    assert r2.status_code == 422 and "FAKE_secret_in_list" not in r2.text


# --- TC-AUTH-06/06b: отзыв (UC-006 1а, FR-021) ---------------------------------------------------

def test_tc06_revocation_effective_on_next_request(baseline):
    victim = logged_in("qa-revoked")
    assert victim.me().status_code == 200
    r = baseline.patch("/admin/users/qa-revoked", json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False
    t0 = time.monotonic()
    assert victim.me().status_code == 401                      # сразу, без ожидания
    assert time.monotonic() - t0 < 5
    assert victim.http.get("/admin/users").status_code == 401
    assert Session().login("qa-revoked").status_code == 401    # LDAP верный, но отозван
    # возврат доступа
    assert baseline.patch("/admin/users/qa-revoked", json={"active": True}).status_code == 200
    assert Session().login("qa-revoked").status_code == 200


def test_tc06b_revocation_kills_all_sessions_only_of_that_user(baseline):
    a1, a2, other = logged_in("qa-revoked"), logged_in("qa-revoked"), logged_in("qa-user")
    baseline.patch("/admin/users/qa-revoked", json={"active": False})
    assert a1.me().status_code == 401 and a2.me().status_code == 401
    assert other.me().status_code == 200
    baseline.patch("/admin/users/qa-revoked", json={"active": True})


# --- TC-AUTH-07: logout, TTL ---------------------------------------------------------------------

def test_tc07_logout_revokes_session_server_side(baseline):
    s = logged_in("qa-user")
    cookie = s.http.cookies.get("re_session")
    assert s.http.post("/auth/logout").status_code == 403      # без CSRF
    assert s.me().status_code == 200
    assert s.post("/auth/logout").status_code == 204
    replay = Session()                                          # повтор старого cookie
    assert replay.http.get("/auth/me", cookies={"re_session": cookie}).status_code == 401


# --- TC-AUTH-09/10: права (FR-031, FR-021) --------------------------------------------------------

def test_tc09_non_admin_forbidden_anonymous_unauthorized(baseline):
    user = logged_in("qa-user")
    anon = Session()
    assert anon.http.get("/admin/users").status_code == 401
    assert anon.http.post("/admin/users", json={"login": "x"}).status_code == 401
    assert user.http.get("/admin/users").status_code == 403
    assert user.post("/admin/users", json={"login": "qa-outsider"}).status_code == 403
    assert user.patch("/admin/users/qa-user", json={"role": "admin"}).status_code == 403   # самоповышение
    assert user.patch("/admin/users/qa-admin", json={"active": False}).status_code == 403
    assert baseline.users()["qa-user"]["role"] == "user"
    assert "qa-outsider" not in baseline.users() or not baseline.users()["qa-outsider"]["active"]


def test_tc09c_role_not_taken_from_login_body(baseline):
    r = Session().http.post("/auth/login", json={
        "login": "qa-user", "password": PASSWORDS["qa-user"], "role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "user"


def test_tc10_sections_depend_on_role_and_follow_demotion(baseline):
    admin_me = baseline.me().json()
    assert [s["key"] for s in admin_me["sections"]] == ["needs", "employees", "resume", "settings"]
    assert baseline.patch("/admin/users/qa-user", json={"role": "admin"}).status_code == 200
    promoted = logged_in("qa-user")
    assert "settings" in [s["key"] for s in promoted.me().json()["sections"]]
    assert promoted.http.get("/admin/users").status_code == 200
    baseline.patch("/admin/users/qa-user", json={"role": "user"})
    # понижение действует на следующем запросе той же сессии
    assert "settings" not in [s["key"] for s in promoted.me().json()["sections"]]
    assert promoted.http.get("/admin/users").status_code == 403


# --- TC-AUTH-11: список допущенных и последний админ ---------------------------------------------

def test_tc11_add_idempotent_normalized_and_validated(baseline):
    r1 = baseline.post("/admin/users", json={"login": "  QA-Outsider "})
    assert r1.status_code == 201 and r1.json()["login"] == "qa-outsider" and r1.json()["active"] is True
    assert baseline.post("/admin/users", json={"login": "qa-outsider"}).status_code == 200  # повтор
    assert Session().login("qa-outsider").status_code == 200   # доступ сразу (FR-021)
    assert baseline.post("/admin/users", json={"login": "   "}).status_code == 422
    assert baseline.post("/admin/users", json={"login": "x" * 129}).status_code == 422
    assert baseline.patch("/admin/users/qa-outsider", json={"active": False}).status_code == 200
    assert Session().login("qa-outsider").status_code == 401


def test_tc11_last_admin_protected(baseline):
    assert baseline.patch("/admin/users/qa-admin", json={"active": False}).status_code == 409
    assert baseline.patch("/admin/users/qa-admin", json={"role": "user"}).status_code == 409
    assert baseline.patch("/admin/users/qa-admin", json={"active": False, "role": "user"}).status_code == 409
    assert baseline.me().status_code == 200                    # сессия цела
    assert baseline.users()["qa-admin"]["active"] is True


def test_tc11b_two_admins_one_can_be_removed(baseline):
    baseline.patch("/admin/users/qa-user", json={"role": "admin"})
    try:
        assert baseline.patch("/admin/users/qa-user", json={"active": False}).status_code == 200
        assert baseline.patch("/admin/users/qa-admin", json={"active": False}).status_code == 409
    finally:
        baseline.patch("/admin/users/qa-user", json={"active": True, "role": "user"})


def test_tc11c_unknown_login_and_no_delete(baseline):
    assert baseline.patch("/admin/users/qa-no-such-user", json={"active": False}).status_code == 404
    assert baseline.patch("/admin/users/qa-user", json={}).status_code == 422
    assert baseline.patch("/admin/users/qa-user", json={"role": "root"}).status_code == 422
    assert baseline.http.delete("/admin/users/qa-user", headers=baseline.csrf()).status_code in (404, 405)
    assert "qa-user" in baseline.users()


def test_tc11d_concurrent_mutual_revoke_keeps_one_admin(baseline):
    """Два админа одновременно отзывают друг друга: должен остаться >= 1 активный админ."""
    outcomes = []
    for _ in range(12):
        setup = logged_in("qa-admin")
        setup.patch("/admin/users/qa-user", json={"active": True, "role": "admin"})
        a, b = logged_in("qa-admin"), logged_in("qa-user")
        hdr_a, hdr_b = a.csrf(), b.csrf()
        barrier, results = threading.Barrier(2), {}

        def run(sess, hdr, target, key):
            barrier.wait()
            results[key] = sess.http.patch(
                f"/admin/users/{target}", json={"active": False}, headers=hdr).status_code

        ts = [threading.Thread(target=run, args=(a, hdr_a, "qa-user", "a")),
              threading.Thread(target=run, args=(b, hdr_b, "qa-admin", "b"))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        survivor = None
        for name in ("qa-admin", "qa-user"):
            s = Session()
            if s.login(name).status_code == 200 and s.http.get("/admin/users").status_code == 200:
                survivor = s
                break
        assert survivor is not None, f"не осталось активного админа, коды: {results}"
        outcomes.append(tuple(sorted(results.values())))
        survivor.patch("/admin/users/qa-admin", json={"active": True, "role": "admin"})
        survivor.patch("/admin/users/qa-user", json={"active": True, "role": "user"})
        assert set(results.values()) <= {200, 401, 409}, results


# --- TC-AUTH-14: CSRF ----------------------------------------------------------------------------

def test_tc14_csrf_required_on_mutating(baseline):
    s = baseline.http
    assert s.post("/admin/users", json={"login": "qa-outsider"}).status_code == 403
    assert s.post("/admin/users", json={"login": "qa-outsider"}, headers={"X-CSRF-Token": ""}).status_code == 403
    assert s.post("/admin/users", json={"login": "qa-outsider"}, headers={"X-CSRF-Token": "bad"}).status_code == 403
    other_token = logged_in("qa-user").csrf()                  # токен чужой сессии
    assert s.post("/admin/users", json={"login": "qa-outsider"}, headers=other_token).status_code == 403
    assert s.get("/admin/users").status_code == 200            # GET без токена
    assert "qa-outsider" not in baseline.users() or not baseline.users()["qa-outsider"]["active"]


# --- TC-AUTH-13: Jira при верном LDAP (FR-029), фейк Jira стенда ---------------------------------

def test_tc13_jira_ok_for_regular_user(baseline):
    r = Session().login("qa-user")
    assert r.status_code == 200
    assert r.json()["jira_status"]["state"] == "ok"
    assert "fake-session" not in r.text                        # токен Jira клиенту не отдаётся


def test_tc13_jira_refusal_does_not_block_login_and_is_shown_as_is(baseline):
    s = Session()
    r = s.login("qa-jirablock")
    assert r.status_code == 200                                # вход в приложение не ломается
    js = r.json()["jira_status"]
    assert js["state"] == "blocked" and js["code"] == "login_blocked"
    assert js["message"].strip()                               # причина показана пользователю
    assert PASSWORDS["qa-jirablock"] not in r.text
    assert s.me().json()["jira_status"]["state"] == "blocked"
    assert s.me().status_code == 200                           # разделы доступны


def test_tc13_jira_refusal_text_shown_verbatim(baseline):
    """FR-029 (KON-69, закрыт): текст отказа из JSON-ответа Jira показывается как есть."""
    js = Session().login("qa-jirablock").json()["jira_status"]
    assert "fake Jira, QA" in js["message"]                    # текст из тела ответа Jira


def test_tc13c_jira_unavailable_state_and_recovery(baseline):
    """Jira недоступна: вход в приложение работает, статус unavailable; после возврата - ok."""
    name = f"{COMPOSE_PROJECT}-fake-jira-1"
    assert _docker("stop", name).returncode == 0
    try:
        s = Session()
        r = s.login("qa-user")
        assert r.status_code == 200
        assert r.json()["jira_status"]["state"] == "unavailable"
        assert r.json()["jira_status"]["code"] == "jira_unavailable"
        assert s.me().status_code == 200
    finally:
        _docker("start", name)
    time.sleep(3)
    assert s.post("/auth/jira-relogin", json={"password": PASSWORDS["qa-user"]}).json()["jira_status"]["state"] == "ok"


def test_tc13b_relogin_with_wrong_then_right_password(baseline):
    s = Session()
    s.login("qa-jirablock")
    from ._stand import CFG

    assert s.http.post("/auth/jira-relogin", json={"password": "FAKE_x"}).status_code == 403  # CSRF
    r = s.post("/auth/jira-relogin", json={"password": "FAKE_wrong_jira_pw"})
    assert r.status_code == 200 and r.json()["jira_status"]["state"] == "blocked"
    assert s.post("/auth/jira-relogin", json={"password": ""}).status_code == 422
    ok = s.post("/auth/jira-relogin", json={"password": CFG["QA_JIRA_RELOGIN_PW"]})
    assert ok.status_code == 200 and ok.json()["jira_status"]["state"] == "ok"
    assert CFG["QA_JIRA_RELOGIN_PW"] not in ok.text
    assert s.me().json()["jira_status"]["state"] == "ok"


# --- TC-AUTH-12: LDAP недоступен (FR-024). Останавливает контейнер LDAP ТОЛЬКО на QA-стенде ------

def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)


def test_tc12_ldap_down_503_without_details_and_recovers(baseline):
    name = f"{COMPOSE_PROJECT}-ldap-test-1"
    open_session = logged_in("qa-user")
    assert _docker("stop", name).returncode == 0
    try:
        r = Session().login("qa-user")
        assert r.status_code == 503
        assert r.json()["detail"] == LDAP_DOWN_TEXT
        assert "ldap-test" not in r.text and "389" not in r.text   # без адресов и деталей
        assert "set-cookie" not in r.headers
        assert open_session.me().status_code == 200            # открытые сессии живут
    finally:
        _docker("start", name)
    deadline, code = time.time() + 60, None
    while time.time() < deadline:
        code = Session().login("qa-user").status_code
        if code == 200:
            break
        time.sleep(2)
    assert code == 200
