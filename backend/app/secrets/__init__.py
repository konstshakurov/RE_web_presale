"""Модуль secrets: шифрование AES-256-GCM и хранение секретов в app.secret_store (ADR-006)."""
from .crypto import (
    DecryptionError,
    SealedSecret,
    SecretsError,
    UnknownKeyIdError,
    open_secret,
    seal,
)
from .store import SecretStore

__all__ = [
    "DecryptionError",
    "SealedSecret",
    "SecretStore",
    "SecretsError",
    "UnknownKeyIdError",
    "open_secret",
    "seal",
]
