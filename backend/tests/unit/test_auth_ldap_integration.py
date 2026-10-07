"""Интеграция с тестовым LDAP (docker compose --profile ldap-test).

Нужны env: TEST_LDAP_URL, TEST_LDAP_BASE_DN, TEST_LDAP_BIND_DN, TEST_LDAP_BIND_PASSWORD,
TEST_LDAP_USER_FILTER, TEST_LDAP_LOGIN, TEST_LDAP_PASSWORD (пользователь создан вручную, ldapadd).
Без них тесты пропускаются; значения только из env.
"""
import os

import pytest

from app.auth import Ldap3Adapter

E = os.environ.get
REQUIRED = ("TEST_LDAP_URL", "TEST_LDAP_BASE_DN", "TEST_LDAP_LOGIN", "TEST_LDAP_PASSWORD")
pytestmark = pytest.mark.skipif(
    not all(E(k) for k in REQUIRED), reason="тестовый LDAP не настроен (TEST_LDAP_*)"
)


@pytest.fixture()
def adapter():
    return Ldap3Adapter(
        E("TEST_LDAP_URL"), E("TEST_LDAP_BASE_DN"), E("TEST_LDAP_USER_FILTER") or "(uid={login})",
        E("TEST_LDAP_BIND_DN", ""), E("TEST_LDAP_BIND_PASSWORD", ""), timeout=5,
    )


def test_valid_credentials(adapter):
    assert adapter.authenticate(E("TEST_LDAP_LOGIN"), E("TEST_LDAP_PASSWORD")) is True


def test_wrong_password(adapter):
    assert adapter.authenticate(E("TEST_LDAP_LOGIN"), E("TEST_LDAP_PASSWORD") + "x") is False


def test_empty_password_is_not_anonymous_bind(adapter):
    assert adapter.authenticate(E("TEST_LDAP_LOGIN"), "") is False


def test_unknown_user_and_injection(adapter):
    assert adapter.authenticate("no-such-user-zz", "x") is False
    assert adapter.authenticate("*", "x") is False
    assert adapter.authenticate("*)(uid=*", "x") is False
