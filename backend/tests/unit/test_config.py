import base64
import os

import pytest

from app.config import ConfigError, load_settings, parse_admin_logins

KEY = base64.b64encode(os.urandom(32)).decode()
DB = "postgresql://app_rw:pw-for-test-only@db:5432/app_db"


def env(**kw):
    base = {"APP_DB_URL": DB, "APP_ENC_KEY": KEY}
    base.update(kw)
    return base


def test_load_ok_defaults():
    s = load_settings(env())
    assert s.enc_key_id == "k1"
    assert s.admin_logins == ()
    assert len(s.enc_key_bytes()) == 32


@pytest.mark.parametrize("missing", ["APP_DB_URL", "APP_ENC_KEY"])
def test_empty_required_fails(missing):
    e = env()
    e[missing] = "  "
    with pytest.raises(ConfigError, match=missing):
        load_settings(e)


@pytest.mark.parametrize("bad", ["not-base64!!", base64.b64encode(b"short").decode()])
def test_bad_key_fails_without_value_in_message(bad):
    with pytest.raises(ConfigError) as ei:
        load_settings(env(APP_ENC_KEY=bad))
    assert bad not in str(ei.value)


def test_repr_hides_secrets():
    s = load_settings(env(LDAP_BIND_PASSWORD="ldap-pass-123"))
    text = repr(s) + str(s)
    for secret in (KEY, DB, "ldap-pass-123", "pw-for-test-only"):
        assert secret not in text
    assert s.secret_values().count("ldap-pass-123") == 1


def test_admin_logins_parsing():
    assert parse_admin_logins("Ivan, petr;IVAN  sidor\n") == ("ivan", "petr", "sidor")
    assert parse_admin_logins("") == ()


def test_ldap_bind_template_is_read():
    tpl = "uid={login},cn=users,dc=example,dc=test"
    assert load_settings(env(LDAP_BIND_TEMPLATE=tpl)).ldap_bind_template == tpl
    assert load_settings(env()).ldap_bind_template == ""
