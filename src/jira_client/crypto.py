# -*- coding: utf-8 -*-
"""Шифрование сессионных токенов (NFR-006: секреты хранятся только зашифрованно).

AES-256-GCM, ключ — 32 байта из переменной окружения, в base64. Ключ в коде
не хранится: ``generate_key`` существует только для первичной настройки и
печатает ключ, который оператор сам уходит в менеджер секретов.

Формат зашифрованного значения: ``base64(nonce || ciphertext|| tag)`` —
одна строка, пригодная для помещения в файл или в БД.
"""

from __future__ import annotations

import base64
import os
from typing import Optional

#: Длина ключа AES-256 в байтах.
KEY_BYTES = 32
#: Стандартная длина nonce для GCM.
NONCE_BYTES = 12


class CryptoError(ValueError):
    """Неверный ключ или повреждённое значение. Значения в текст не попадают."""


def generate_key() -> str:
    """Новый случайный ключ в base64. Вызывается оператором вручную."""
    return base64.b64encode(os.urandom(KEY_BYTES)).decode("ascii")


def load_key(raw: Optional[str]) -> bytes:
    """Разобрать ключ из base64-строки."""
    if not raw:
        raise CryptoError("Ключ шифрования не задан (JIRA_TOKEN_ENCRYPTION_KEY)")
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except Exception as exc:  # binascii.Error и прочие
        raise CryptoError("Ключ должен быть корректной base64-строкой") from exc
    if len(key) != KEY_BYTES:
        raise CryptoError(
            "Ключ должен декодироваться ровно в %d байт, получено %d"
            % (KEY_BYTES, len(key))
        )
    return key


class TokenVault:
    """Шифрование и расшифровка сессионного токена."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise CryptoError("Неверная длина ключа: ожидалось %d байт" % KEY_BYTES)
        self._key = key

    @classmethod
    def from_env(cls, raw: Optional[str] = None) -> "TokenVault":
        if raw is None:
            raw = os.environ.get("JIRA_TOKEN_ENCRYPTION_KEY")
        return cls(load_key(raw))

    @classmethod
    def from_base64(cls, raw: str) -> "TokenVault":
        return cls(load_key(raw))

    def seal(self, plaintext: str, *, aad: bytes = b"jira-session-token") -> str:
        """Зашифровать токен в одну base64-строку."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if plaintext is None or plaintext == "":
            raise CryptoError("Пустой токен шифровать нельзя")
        nonce = os.urandom(NONCE_BYTES)
        blob = AESGCM(self._key).encrypt(nonce, plaintext.encode("utf-8"), aad)
        return base64.b64encode(nonce + blob).decode("ascii")

    def open(self, sealed: str, *, aad: bytes = b"jira-session-token") -> str:
        """Расшифровать токен."""
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if not sealed:
            raise CryptoError("Пустое зашифрованное значение")
        try:
            raw = base64.b64decode(sealed.strip(), validate=True)
        except Exception as exc:
            raise CryptoError("Значение не является корректным base64") from exc
        if len(raw) <= NONCE_BYTES:
            raise CryptoError("Повреждённое значение: слишком короткое")
        nonce, body = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
        try:
            plain = AESGCM(self._key).decrypt(nonce, body, aad)
        except InvalidTag as exc:
            # Секрет не показываем ни текстом, ни в repr.
            raise CryptoError(
                "Не расшифровано: неверный ключ, подмена данных или другой AAD"
            ) from exc
        return plain.decode("utf-8")