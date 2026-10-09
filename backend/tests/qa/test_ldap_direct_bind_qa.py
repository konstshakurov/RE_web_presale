"""QA-4 (KON-56): прямой bind по LDAP_BIND_TEMPLATE (коммит 6265243), мок-тесты без сети.

Дополняют unit-набор Dev: граничные символы в логине, «ни одного соединения на отклонённый
ввод», сбои сети, чтение профиля только своей записи. Кейсы: TC-LDAP-* в qa/auth_cases.md.
"""
from __future__ import annotations

import pytest
from ldap3.core.exceptions import LDAPSocketOpenError

from app.auth import Ldap3Adapter, LdapUnavailable
from app.auth.ldap_adapter import build_bind_dn

TEMPLATE = "uid={login},cn=users,cn=accounts,dc=qa,dc=example,dc=test"
OWN_DN = "uid=ivan,cn=users,cn=accounts,dc=qa,dc=example,dc=test"
FAKE_PW = "FAKE_ldap_pw_1"


class _Entry:
    def __init__(self, attrs):
        self.entry_attributes_as_dict = attrs


class RecConn:
    """Фиктивный Connection: фиксирует DN bind и все search; пароль сверяется по таблице."""

    made: list = []
    table: dict = {}
    attrs = {"cn": ["QA Ivan Fiction"], "mail": ["ivan@qa.example.test"]}
    bind_error: Exception | None = None

    def __init__(self, server, user=None, password=None, **kw):
        self.user, self.password, self.bound, self.entries, self.searches = (
            user, password, False, [], [])
        RecConn.made.append(self)

    def bind(self):
        from ldap3.core.exceptions import LDAPInvalidCredentialsResult

        if RecConn.bind_error:
            raise RecConn.bind_error
        if not self.password or RecConn.table.get(self.user) != self.password:
            raise LDAPInvalidCredentialsResult()
        self.bound = True

    def unbind(self):
        self.bound = False

    def search(self, base, flt, **kw):
        self.searches.append((base, flt, kw))
        self.entries = [_Entry(RecConn.attrs)]


@pytest.fixture()
def adapter(monkeypatch):
    RecConn.made, RecConn.bind_error = [], None
    RecConn.table = {OWN_DN: FAKE_PW}
    monkeypatch.setattr("app.auth.ldap_adapter.Connection", RecConn)
    return Ldap3Adapter("ldap://h:389", "", "", bind_template=TEMPLATE)


def test_ldap01_success_binds_exact_dn_and_reads_only_own_entry(adapter):
    p = adapter.authenticate_profile("ivan", FAKE_PW)
    assert (p.full_name, p.mail) == ("QA Ivan Fiction", "ivan@qa.example.test")
    assert [c.user for c in RecConn.made] == [OWN_DN]  # без служебной учётки и поиска
    base, flt, kw = RecConn.made[0].searches[0]
    assert base == OWN_DN and flt == "(objectClass=*)"
    assert kw.get("search_scope") == "BASE" and not RecConn.made[0].bound


@pytest.mark.parametrize("login,password", [
    ("", FAKE_PW), ("ivan", ""), ("", ""), ("iv\x00an", FAKE_PW), ("ivan", FAKE_PW + "\x00"),
    ("ivan", "p" * 1025),
])
def test_ldap02_rejected_input_opens_no_connection(adapter, login, password):
    assert adapter.authenticate_profile(login, password) is None
    assert RecConn.made == []  # пустой пароль не должен дойти до сервера как анонимный bind


def test_ldap02b_max_length_password_is_still_sent(adapter):
    RecConn.table[OWN_DN] = "p" * 1024
    assert adapter.authenticate("ivan", "p" * 1024) is True


@pytest.mark.parametrize("login", [
    "ivan,cn=admins", "ivan+uid=root", 'iv"an', "iv\\an", "#ivan", " ivan", "ivan ", "iv<an>",
    "iv;an", "*", "ivan)(uid=*",
])
def test_ldap03_special_chars_cannot_change_dn_structure(adapter, login):
    dn = build_bind_dn(TEMPLATE, login)
    # шаблон после подстановки остаётся одним RDN uid=... и теми же родителями
    assert dn.endswith(",cn=users,cn=accounts,dc=qa,dc=example,dc=test")
    head = dn[: -len(",cn=users,cn=accounts,dc=qa,dc=example,dc=test")]
    import re
    assert not re.search(r"(?<!\\),", head), f"неэкранированная запятая: {dn}"
    assert not re.search(r"(?<!\\)\+", head), f"неэкранированный плюс: {dn}"
    assert adapter.authenticate(login, FAKE_PW) is False
    assert all(c.user != OWN_DN for c in RecConn.made)


def test_ldap04_wrong_password_false_and_connection_closed(adapter):
    assert adapter.authenticate("ivan", "FAKE_wrong") is False
    assert not any(c.bound for c in RecConn.made)


def test_ldap05_unknown_user_is_false_not_unavailable(adapter):
    assert adapter.authenticate("nobody", FAKE_PW) is False


@pytest.mark.parametrize("exc", [LDAPSocketOpenError("x"), OSError("net"), TimeoutError()])
def test_ldap06_network_failure_is_unavailable_without_details(adapter, exc):
    RecConn.bind_error = exc
    with pytest.raises(LdapUnavailable) as e:
        adapter.authenticate("ivan", FAKE_PW)
    assert FAKE_PW not in str(e.value) and "net" not in str(e.value)


def test_ldap07_login_with_template_is_case_preserved_in_dn(adapter):
    """Адаптер не меняет регистр: нормализация (lower) делается в роутере (normalize_login)."""
    assert build_bind_dn(TEMPLATE, "IVAN").startswith("uid=IVAN,")


def test_ldap08_password_not_in_repr_or_exception_chain(adapter):
    RecConn.bind_error = OSError(f"boom {FAKE_PW}")  # сервер/стек может вернуть что угодно
    with pytest.raises(LdapUnavailable) as e:
        adapter.authenticate("ivan", FAKE_PW)
    assert FAKE_PW not in repr(e.value)
    assert e.value.__cause__ is None  # исходная ошибка с деталями не пробрасывается
