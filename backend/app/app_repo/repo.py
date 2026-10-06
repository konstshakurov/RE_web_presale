"""SQL к app.app_user, app.app_session, app.secret_store. Параметры только через плейсхолдеры."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from ..secrets.crypto import SealedSecret


@dataclass(frozen=True)
class AppUser:
    ldap_login: str
    role: str
    active: bool


class AppRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # --- app_user (FR-021, FR-024) ---

    def ensure_admin(self, login: str) -> bool:
        """Создать админа, если такого логина нет. True, если строка создана (идемпотентно)."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "INSERT INTO app.app_user (ldap_login, role, active) VALUES (%s, 'admin', true) "
                "ON CONFLICT (ldap_login) DO NOTHING",
                (login,),
            )
            return cur.rowcount == 1

    def get_user(self, login: str) -> AppUser | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT ldap_login, role, active FROM app.app_user WHERE ldap_login = %s",
                (login,),
            ).fetchone()
        return AppUser(*row) if row else None

    def list_users(self) -> list[AppUser]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT ldap_login, role, active FROM app.app_user ORDER BY ldap_login"
            ).fetchall()
        return [AppUser(*r) for r in rows]

    # --- app_session (ADR-008): хранится только SHA-256 идентификатора ---

    def create_session(self, session_hash: bytes, login: str, expires_at: datetime) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO app.app_session (session_hash, ldap_login, expires_at) "
                "VALUES (%s, %s, %s)",
                (session_hash, login, expires_at),
            )

    def get_active_session(self, session_hash: bytes) -> tuple[str, datetime] | None:
        """(ldap_login, expires_at) для непросроченной и неотозванной сессии."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT ldap_login, expires_at FROM app.app_session "
                "WHERE session_hash = %s AND revoked_at IS NULL AND expires_at > now()",
                (session_hash,),
            ).fetchone()
        return (row[0], row[1]) if row else None

    def touch_session(self, session_hash: bytes) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE app.app_session SET last_seen_at = now() WHERE session_hash = %s",
                (session_hash,),
            )

    def revoke_session(self, session_hash: bytes) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE app.app_session SET revoked_at = now() "
                "WHERE session_hash = %s AND revoked_at IS NULL",
                (session_hash,),
            )

    def revoke_user_sessions(self, login: str) -> int:
        """Немедленный отзыв всех сессий пользователя (FR-021)."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE app.app_session SET revoked_at = now() "
                "WHERE ldap_login = %s AND revoked_at IS NULL",
                (login,),
            )
            return cur.rowcount

    # --- secret_store, kind='jira_session' (один токен на пользователя) ---

    def upsert_jira_session_secret(self, owner_login: str, sealed: SealedSecret) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO app.secret_store (kind, owner_login, ciphertext, nonce, key_id) "
                "VALUES ('jira_session', %s, %s, %s, %s) "
                "ON CONFLICT (owner_login) WHERE kind = 'jira_session' DO UPDATE SET "
                "ciphertext = EXCLUDED.ciphertext, nonce = EXCLUDED.nonce, "
                "key_id = EXCLUDED.key_id, created_at = now(), updated_at = now()",
                (owner_login, sealed.ciphertext, sealed.nonce, sealed.key_id),
            )

    def get_jira_session_secret(self, owner_login: str) -> SealedSecret | None:
        from ..secrets.crypto import SealedSecret

        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT ciphertext, nonce, key_id FROM app.secret_store "
                "WHERE kind = 'jira_session' AND owner_login = %s",
                (owner_login,),
            ).fetchone()
        if not row:
            return None
        return SealedSecret(ciphertext=bytes(row[0]), nonce=bytes(row[1]), key_id=row[2])

    def delete_jira_session_secret(self, owner_login: str) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM app.secret_store WHERE kind = 'jira_session' AND owner_login = %s",
                (owner_login,),
            )
            return cur.rowcount == 1
