"""seal/open AES-256-GCM под схему app.secret_store: ciphertext (с тегом), nonce 12 байт, key_id.

Ключ приходит из env (config.py), в коде его нет. AAD связывает шифртекст со строкой
(kind и владелец), чтобы запись нельзя было подменить другой.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
KEY_BYTES = 32


class SecretsError(Exception):
    """База ошибок модуля. Значения секретов в сообщения не попадают."""


class DecryptionError(SecretsError):
    """Не расшифровано: неверный ключ, подмена данных или другой AAD."""


class UnknownKeyIdError(SecretsError):
    """Запись зашифрована ключом с key_id, которого нет в связке."""


@dataclass(frozen=True)
class SealedSecret:
    ciphertext: bytes
    nonce: bytes
    key_id: str

    def __repr__(self) -> str:
        return f"SealedSecret(key_id={self.key_id!r}, len={len(self.ciphertext)})"


def seal(plaintext: str, key: bytes, key_id: str, aad: bytes = b"") -> SealedSecret:
    if not plaintext:
        raise SecretsError("Пустой секрет шифровать нельзя")
    if len(key) != KEY_BYTES:
        raise SecretsError(f"Ключ должен быть {KEY_BYTES} байт")
    nonce = os.urandom(NONCE_BYTES)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return SealedSecret(ciphertext=ct, nonce=nonce, key_id=key_id)


def open_secret(
    sealed: SealedSecret, keys: dict[str, bytes], aad: bytes = b""
) -> str:
    """Расшифровать; ``keys`` — связка key_id -> ключ (поддержка ротации)."""
    key = keys.get(sealed.key_id)
    if key is None:
        raise UnknownKeyIdError(f"Нет ключа с key_id={sealed.key_id!r}")
    try:
        plain = AESGCM(key).decrypt(sealed.nonce, sealed.ciphertext, aad)
    except (InvalidTag, ValueError):
        raise DecryptionError(
            "Не расшифровано: неверный ключ, подмена данных или другой AAD"
        ) from None
    return plain.decode("utf-8")
