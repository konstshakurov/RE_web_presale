"""Лимит размера файла (FR-022, ADR-009): app_db.app.app_setting.max_file_mb, иначе CV_MAX_FILE_MB,
иначе 10 МБ. Потолок 10 МБ зашит CHECK в cv_db (V001): выше поднять можно только миграцией.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

SETTING_KEY = "max_file_mb"
MB = 1024 * 1024
DEFAULT_MB = 10
HARD_MAX_MB = 10
MIN_MB = 1


def parse_mb(raw: str | None) -> int | None:
    """Целое число МБ в пределах 1..10; всё остальное - None."""
    try:
        value = int((raw or "").strip())
    except ValueError:
        return None
    return value if MIN_MB <= value <= HARD_MAX_MB else None


class CvLimits:
    def __init__(self, pool: ConnectionPool, env_default_mb: int | None = None) -> None:
        self._pool = pool
        ok = env_default_mb is not None and MIN_MB <= env_default_mb <= HARD_MAX_MB
        self._env_mb = env_default_mb if ok else DEFAULT_MB

    def max_mb(self) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM app.app_setting WHERE key = %s", (SETTING_KEY,)
            ).fetchone()
        return parse_mb(row[0]) if row and parse_mb(row[0]) else self._env_mb

    def max_bytes(self) -> int:
        return self.max_mb() * MB

    def set_max_mb(self, value: int) -> None:
        if not MIN_MB <= value <= HARD_MAX_MB:
            raise ValueError("max_file_mb вне 1..10")
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO app.app_setting (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (SETTING_KEY, str(value)),
            )
