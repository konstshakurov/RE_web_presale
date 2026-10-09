"""QA-4 (KON-56): интеграция с одноразовым тестовым LDAP (qa/ldap/load_seed.sh up).

Без переменных QA_LDAP_* тесты пропускаются. Значения только из окружения (локальный .env):
QA_LDAP_URL (по умолчанию ldap://127.0.0.1:2389/), QA_LDAP_BIND_TEMPLATE (по умолчанию
uid={login},cn=users,cn=accounts,dc=qa,dc=example,dc=test), QA_LDAP_PW_ADMIN/USER/OUTSIDER/REVOKED.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.auth import Ldap3Adapter, LdapUnavailable

from ._support import MemRepo, build_app, csrf_headers, login

E = os.environ.get
PW = {k: E(f"QA_LDAP_PW_{k.upper()}") for k in ("admin", "user", "outsider", "revoked")}
pytestmark = pytest.mark.skipif(
    not all(PW.values()), reason="одноразовый тестовый LDAP не поднят (QA_LDAP_PW_*)"
)
URL = E("QA_LDAP_URL") or "ldap://127.0.0.1:2389/"
TEMPLATE = E("QA_LDAP_BIND_TEMPLATE") or "uid={login},cn=users,cn=accounts,dc=qa,dc=example,dc=test"


@pytest.fixture()
def adapter():
    return Ldap3Adapter(URL, "", "", bind_template=TEMPLATE, timeout=5)


def test_c01_valid_credentials_and_profile_read_after_bind(adapter):
    p = adapter.authenticate_profile("qa-user", PW["user"])
    assert p is not None
    assert p.full_name == "QA User Fiction" and p.mail == "qa-user@qa.example.test"


def test_c01b_profile_without_mail_attribute_has_empty_mail(adapter):
    p = adapter.authenticate_profile("qa-revoked", PW["revoked"])
    assert p is not None and p.full_name == "QA Revoked Fiction" and p.mail == ""


def test_c02_wrong_password_empty_password_unknown_user(adapter):
    assert adapter.authenticate("qa-user", PW["user"] + "x") is False
    assert adapter.authenticate("qa-user", "") is False  # не анонимный bind
    assert adapter.authenticate("qa-nobody", "FAKE_x") is False


def test_c02b_password_of_another_user_is_rejected(adapter):
    assert adapter.authenticate("qa-user", PW["admin"]) is False


@pytest.mark.parametrize("login_", [
    "*", "qa-*", "qa-user)(uid=*", "qa-user,cn=users,cn=accounts,dc=qa,dc=example,dc=test",
    "qa-user+uid=qa-admin", "qa-user\\", "", " ",
])
def test_c03_injection_and_dn_tricks_denied(adapter, login_):
    assert adapter.authenticate(login_, PW["user"]) is False
    assert adapter.authenticate(login_, "x") is False


def test_c04_login_is_case_insensitive_for_uid_on_server(adapter):
    """uid в OpenLDAP сравнивается без учёта регистра; приложение всё равно хранит lower."""
    assert adapter.authenticate("QA-USER", PW["user"]) is True


def test_c05_unreachable_server_is_unavailable():
    dead = Ldap3Adapter("ldap://127.0.0.1:1/", "", "", bind_template=TEMPLATE, timeout=1)
    with pytest.raises(LdapUnavailable):
        dead.authenticate("qa-user", PW["user"])


@pytest.fixture()
def stand(adapter):
    """Приложение с настоящим адаптером LDAP; список допущенных в памяти."""
    repo = MemRepo()
    repo.put("qa-admin", "admin")
    repo.put("qa-user", "user")
    repo.put("qa-revoked", "user")
    app, _ = build_app(adapter, repo)
    return app, repo


def test_c06_e2e_in_list_ok_not_in_list_denied(stand):
    app, repo = stand
    c = TestClient(app)
    assert login(c, "qa-user", PW["user"]).status_code == 200
    out = TestClient(app)
    r = login(out, "qa-outsider", PW["outsider"])  # верный LDAP, нет в списке
    assert r.status_code == 401 and "re_session" not in out.cookies
    assert login(TestClient(app), "QA-User", PW["user"]).status_code == 200  # нормализация


def test_c07_e2e_revoke_with_open_session_and_last_admin(stand):
    app, repo = stand
    admin, victim = TestClient(app), TestClient(app)
    assert login(admin, "qa-admin", PW["admin"]).status_code == 200
    assert login(victim, "qa-revoked", PW["revoked"]).status_code == 200
    r = admin.patch("/admin/users/qa-revoked", json={"active": False}, headers=csrf_headers(admin))
    assert r.status_code == 200
    assert victim.get("/auth/me").status_code == 401
    assert login(TestClient(app), "qa-revoked", PW["revoked"]).status_code == 401
    r = admin.patch("/admin/users/qa-admin", json={"active": False}, headers=csrf_headers(admin))
    assert r.status_code == 409
