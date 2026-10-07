"""Серверные сессии (ADR-008): случайный id в cookie, в БД только SHA-256; CSRF-токен."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def hash_session_id(session_id: str) -> bytes:
    return hashlib.sha256(session_id.encode("utf-8")).digest()


def expires_at(ttl_hours: float, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + timedelta(hours=ttl_hours)


def csrf_token(enc_key: bytes, session_id: str) -> str:
    """Токен привязан к сессии: HMAC-SHA256(ключ приложения, id сессии)."""
    return hmac.new(enc_key, b"csrf:" + session_id.encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_valid(enc_key: bytes, session_id: str, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(csrf_token(enc_key, session_id), presented)
