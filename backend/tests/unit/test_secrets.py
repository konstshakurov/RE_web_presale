import os

import pytest

from app.secrets import (
    DecryptionError,
    SealedSecret,
    SecretsError,
    SecretStore,
    UnknownKeyIdError,
    open_secret,
    seal,
)

TOKEN = "FAKE_JSESSIONID-VALUE-0123456789"
KEY = os.urandom(32)


class MemRepo:
    """Подмена AppRepo: хранит ровно то, что ушло бы в secret_store."""

    def __init__(self):
        self.rows: dict[str, SealedSecret] = {}

    def upsert_jira_session_secret(self, owner, sealed):
        self.rows[owner] = sealed

    def get_jira_session_secret(self, owner):
        return self.rows.get(owner)

    def delete_jira_session_secret(self, owner):
        return self.rows.pop(owner, None) is not None


def test_round_trip():
    s = seal(TOKEN, KEY, "k1", b"a")
    assert open_secret(s, {"k1": KEY}, b"a") == TOKEN
    assert len(s.nonce) == 12 and len(s.ciphertext) >= 16 and s.key_id == "k1"
    assert TOKEN.encode() not in s.ciphertext


def test_nonce_differs_for_same_value():
    a, b = seal(TOKEN, KEY, "k1"), seal(TOKEN, KEY, "k1")
    assert a.nonce != b.nonce and a.ciphertext != b.ciphertext


def test_wrong_key_typed_error_without_value():
    s = seal(TOKEN, KEY, "k1")
    with pytest.raises(DecryptionError) as ei:
        open_secret(s, {"k1": os.urandom(32)})
    assert TOKEN not in str(ei.value) and TOKEN not in repr(ei.value)
    assert isinstance(ei.value, SecretsError)


def test_wrong_aad_and_tamper_fail():
    s = seal(TOKEN, KEY, "k1", b"jira_session:ivan")
    with pytest.raises(DecryptionError):
        open_secret(s, {"k1": KEY}, b"jira_session:petr")
    bad = SealedSecret(bytes([s.ciphertext[0] ^ 1]) + s.ciphertext[1:], s.nonce, "k1")
    with pytest.raises(DecryptionError):
        open_secret(bad, {"k1": KEY}, b"jira_session:ivan")


def test_unknown_key_id():
    with pytest.raises(UnknownKeyIdError):
        open_secret(seal(TOKEN, KEY, "k1"), {"k2": KEY})


def test_empty_secret_rejected():
    with pytest.raises(SecretsError):
        seal("", KEY, "k1")


def test_repr_has_no_ciphertext():
    s = seal(TOKEN, KEY, "k1")
    assert TOKEN not in repr(s) and s.ciphertext.hex() not in repr(s)


def test_store_upsert_one_token_per_user_and_no_plaintext():
    repo = MemRepo()
    store = SecretStore(repo, KEY, "k1")
    store.save_jira_session("ivan", TOKEN)
    store.save_jira_session("ivan", TOKEN + "2")
    assert len(repo.rows) == 1
    row = repo.rows["ivan"]
    assert TOKEN.encode() not in row.ciphertext + row.nonce
    assert store.load_jira_session("ivan") == TOKEN + "2"
    assert store.load_jira_session("nobody") is None
    assert store.delete_jira_session("ivan") and store.load_jira_session("ivan") is None


def test_store_row_not_movable_between_owners():
    repo = MemRepo()
    store = SecretStore(repo, KEY, "k1")
    store.save_jira_session("ivan", TOKEN)
    repo.rows["petr"] = repo.rows["ivan"]
    with pytest.raises(DecryptionError):
        store.load_jira_session("petr")
