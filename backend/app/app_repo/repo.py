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
    display_name: str | None = None  # ФИО из LDAP (cn), KON-60


@dataclass(frozen=True)
class AppUserDetail:
    ldap_login: str
    role: str
    active: bool
    created_at: datetime
    updated_at: datetime


#: Итоги update_user
UPDATED, NOT_FOUND, LAST_ADMIN = "updated", "not_found", "last_admin"

_DETAIL = "ldap_login, role, active, created_at, updated_at"


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
                "SELECT ldap_login, role, active, display_name FROM app.app_user "
                "WHERE ldap_login = %s",
                (login,),
            ).fetchone()
        return AppUser(*row) if row else None

    def set_display_name(self, login: str, display_name: str) -> None:
        """Сохранить ФИО из LDAP (при каждом входе); пустое не пишется, прежнее остаётся."""
        name = display_name.strip()[:256]
        if not name:
            return
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE app.app_user SET display_name = %s "
                "WHERE ldap_login = %s AND display_name IS DISTINCT FROM %s",
                (name, login, name),
            )

    def list_users(self) -> list[AppUser]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT ldap_login, role, active FROM app.app_user ORDER BY ldap_login"
            ).fetchall()
        return [AppUser(*r) for r in rows]

    def list_users_detail(self) -> list[AppUserDetail]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_DETAIL} FROM app.app_user ORDER BY ldap_login"
            ).fetchall()
        return [AppUserDetail(*r) for r in rows]

    def get_user_detail(self, login: str) -> AppUserDetail | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_DETAIL} FROM app.app_user WHERE ldap_login = %s", (login,)
            ).fetchone()
        return AppUserDetail(*row) if row else None

    def add_user(self, login: str) -> tuple[AppUserDetail, bool]:
        """Добавить в список допущенных; ранее отозванный снова active=true (роль сохраняется).
        (пользователь, изменено): False, если уже был активен (идемпотентно)."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO app.app_user (ldap_login, role, active) VALUES (%s, 'user', true) "
                f"ON CONFLICT (ldap_login) DO NOTHING RETURNING {_DETAIL}",
                (login,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "UPDATE app.app_user SET active = true, updated_at = now() "
                    f"WHERE ldap_login = %s AND active = false RETURNING {_DETAIL}",
                    (login,),
                ).fetchone()
            if row is not None:
                return AppUserDetail(*row), True
            row = conn.execute(
                f"SELECT {_DETAIL} FROM app.app_user WHERE ldap_login = %s", (login,)
            ).fetchone()
        return AppUserDetail(*row), False

    def update_user(
        self, login: str, active: bool | None = None, role: str | None = None
    ) -> str:
        """Изменить active/role одной транзакцией; при active=false сессии отзываются тут же.

        Нельзя оставить систему без активного админа: активные админы блокируются
        FOR UPDATE, поэтому два параллельных отзыва не обойдут проверку. Итог:
        UPDATED | NOT_FOUND | LAST_ADMIN.
        """
        with self._pool.connection() as conn:
            admins = [
                r[0]
                for r in conn.execute(
                    "SELECT ldap_login FROM app.app_user "
                    "WHERE role = 'admin' AND active = true ORDER BY ldap_login FOR UPDATE"
                ).fetchall()
            ]
            cur = conn.execute(
                "SELECT role, active FROM app.app_user WHERE ldap_login = %s FOR UPDATE", (login,)
            ).fetchone()
            if cur is None:
                return NOT_FOUND
            new_role = cur[0] if role is None else role
            new_active = cur[1] if active is None else active
            if login in admins and not (new_role == "admin" and new_active) and len(admins) <= 1:
                return LAST_ADMIN
            conn.execute(
                "UPDATE app.app_user SET role = %s, active = %s, updated_at = now() "
                "WHERE ldap_login = %s",
                (new_role, new_active, login),
            )
            if not new_active:
                conn.execute(
                    "UPDATE app.app_session SET revoked_at = now() "
                    "WHERE ldap_login = %s AND revoked_at IS NULL",
                    (login,),
                )
        return UPDATED

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

    # --- secret_store, kind='llm_key' (один ключ на провайдера, KON-37) ---

    def upsert_llm_key_secret(self, provider_id: int, sealed: SealedSecret) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO app.secret_store (kind, provider_id, ciphertext, nonce, key_id) "
                "VALUES ('llm_key', %s, %s, %s, %s) "
                "ON CONFLICT (provider_id) WHERE kind = 'llm_key' DO UPDATE SET "
                "ciphertext = EXCLUDED.ciphertext, nonce = EXCLUDED.nonce, "
                "key_id = EXCLUDED.key_id, updated_at = now()",
                (provider_id, sealed.ciphertext, sealed.nonce, sealed.key_id),
            )

    def get_llm_key_secret(self, provider_id: int) -> SealedSecret | None:
        from ..secrets.crypto import SealedSecret

        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT ciphertext, nonce, key_id FROM app.secret_store "
                "WHERE kind = 'llm_key' AND provider_id = %s",
                (provider_id,),
            ).fetchone()
        if not row:
            return None
        return SealedSecret(ciphertext=bytes(row[0]), nonce=bytes(row[1]), key_id=row[2])

    def delete_llm_key_secret(self, provider_id: int) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM app.secret_store WHERE kind = 'llm_key' AND provider_id = %s",
                (provider_id,),
            )
            return cur.rowcount == 1
