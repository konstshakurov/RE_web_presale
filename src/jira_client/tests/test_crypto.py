# -*- coding: utf-8 -*-
"""Шифрование токена: AES-256-GCM, ключ из env (NFR-006)."""

from __future__ import annotations

import base64
import os

import pytest

from jira_client.crypto import KEY_BYTES, CryptoError, TokenVault, generate_key, load_key

FAKE_TOKEN = "ТЕСТОВЫЙ-СЕССИОННЫЙ-ТОКЕН"


@pytest.fixture
def key_b64() -> str:
    return generate_key()


def test_generated_key_has_correct_length(key_b64: str):
    assert len(base64.b64decode(key_b64)) == KEY_BYTES == 32


def test_generated_keys_are_unique():
    assert generate_key() != generate_key()


def test_seal_open_round_trip(key_b64: str):
    vault = TokenVault.from_base64(key_b64)
    sealed = vault.seal(FAKE_TOKEN)
    assert sealed != FAKE_TOKEN
    assert vault.open(sealed) == FAKE_TOKEN


def test_seal_is_non_deterministic(key_b64: str):
    """Новый nonce на каждое шифрование — иначе одинаковые токены сравнимы."""
    vault = TokenVault.from_base64(key_b64)
    assert vault.seal(FAKE_TOKEN) != vault.seal(FAKE_TOKEN)


def test_tampered_ciphertext_is_rejected(key_b64: str):
    from cryptography.exceptions import InvalidTag

    vault = TokenVault.from_base64(key_b64)
    sealed = vault.seal(FAKE_TOKEN)
    raw = bytearray(base64.b64decode(sealed))
    raw[-1] ^= 0x01
    with pytest.raises(CryptoError):
        vault.open(base64.b64encode(bytes(raw)).decode())


def test_wrong_key_is_rejected(key_b64: str):
    vault = TokenVault.from_base64(key_b64)
    sealed = vault.seal(FAKE_TOKEN)
    other = TokenVault.from_base64(generate_key())
    with pytest.raises(CryptoError):
        other.open(sealed)


def test_wrong_aad_is_rejected(key_b64: str):
    vault = TokenVault.from_base64(key_b64)
    sealed = vault.seal(FAKE_TOKEN, aad=b"other")
    with pytest.raises(CryptoError):
        vault.open(sealed, aad=b"jira-session-token")


def test_crypto_error_does_not_leak_value(key_b64: str):
    vault = TokenVault.from_base64(key_b64)
    sealed = vault.seal(FAKE_TOKEN)
    try:
        TokenVault.from_base64(generate_key()).open(sealed)
    except CryptoError as exc:
        assert FAKE_TOKEN not in str(exc)


def test_empty_token_is_rejected(key_b64: str):
    vault = TokenVault.from_base64(key_b64)
    with pytest.raises(CryptoError):
        vault.seal("")
    with pytest.raises(CryptoError):
        vault.seal(None)


def test_empty_sealed_value_is_rejected(key_b64: str):
    with pytest.raises(CryptoError):
        TokenVault.from_base64(key_b64).open("")


def test_short_value_is_rejected(key_b64: str):
    with pytest.raises(CryptoError):
        TokenVault.from_base64(key_b64).open(base64.b64encode(b"1234").decode())


def test_non_base64_value_is_rejected(key_b64: str):
    with pytest.raises(CryptoError):
        TokenVault.from_base64(key_b64).open("не base64!!!")


class TestKeyLoading:
    def test_missing_key(self):
        with pytest.raises(CryptoError) as exc:
            load_key(None)
        assert "JIRA_TOKEN_ENCRYPTION_KEY" in str(exc.value)

    def test_wrong_length(self):
        with pytest.raises(CryptoError) as exc:
            load_key(base64.b64encode(b"too-short").decode())
        assert "32" in str(exc.value)

    def test_invalid_base64(self):
        with pytest.raises(CryptoError):
            load_key("!!!not base64!!!")

    def test_from_env_reads_variable(self, monkeypatch, key_b64: str):
        monkeypatch.setenv("JIRA_TOKEN_ENCRYPTION_KEY", key_b64)
        vault = TokenVault.from_env()
        assert vault.seal(FAKE_TOKEN) is not None

    def test_from_env_prefers_explicit_value(self, monkeypatch, key_b64: str):
        monkeypatch.setenv("JIRA_TOKEN_ENCRYPTION_KEY", generate_key())
        vault = TokenVault.from_env(key_b64)
        assert vault.open(vault.seal(FAKE_TOKEN)) == FAKE_TOKEN

    def test_key_is_not_written_to_disk(self, key_b64: str, tmp_path):
        """Ключ обязан отсутствовать в репозитории (критерий NFR-006 (2))."""
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        needle = key_b64
        for dirpath, _dirnames, filenames in os.walk(repo):
            if os.sep + ".git" in dirpath:
                continue
            for name in filenames:
                path = os.path.join(dirpath, name)
                if name.endswith((".pyc", ".png", ".jpg", ".zip")):
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                        assert needle not in handle.read(), "ключ найден в %s" % path
                except OSError:
                    continue