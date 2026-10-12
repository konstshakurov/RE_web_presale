"""Настройки двухступенчатой оценки (администратор): размер глубокой оценки N и размер пакета
скрининга. Хранятся в app_db.app.app_setting (как лимит файла CV); вне границ или не число -
значение по умолчанию."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .screening import (
    DEEP_SIZE_DEFAULT,
    DEEP_SIZE_MAX,
    DEEP_SIZE_MIN,
    SCREEN_BATCH_DEFAULT,
    SCREEN_BATCH_MAX,
    SCREEN_BATCH_MIN,
)

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

KEY_DEEP = "match_deep_size"
KEY_SCREEN = "match_screen_batch"


def _parse(raw: str | None, lo: int, hi: int, default: int) -> int:
    try:
        value = int((raw or "").strip())
    except ValueError:
        return default
    return value if lo <= value <= hi else default


def check(deep_size: int | None, screen_batch: int | None) -> None:
    if deep_size is not None and not DEEP_SIZE_MIN <= deep_size <= DEEP_SIZE_MAX:
        raise ValueError(f"deep_size вне {DEEP_SIZE_MIN}..{DEEP_SIZE_MAX}")
    if screen_batch is not None and not SCREEN_BATCH_MIN <= screen_batch <= SCREEN_BATCH_MAX:
        raise ValueError(f"screen_batch вне {SCREEN_BATCH_MIN}..{SCREEN_BATCH_MAX}")


class StaticSettings:
    """Значения в памяти (тесты и запуск без app_db)."""

    def __init__(self, deep_size: int = DEEP_SIZE_DEFAULT,
                 screen_batch: int = SCREEN_BATCH_DEFAULT) -> None:
        self._deep = deep_size
        self._screen = screen_batch

    def deep_size(self) -> int:
        return self._deep

    def screen_batch(self) -> int:
        return self._screen

    def set(self, deep_size: int | None = None, screen_batch: int | None = None) -> None:
        check(deep_size, screen_batch)
        if deep_size is not None:
            self._deep = deep_size
        if screen_batch is not None:
            self._screen = screen_batch


class AppSettings:
    """Значения из app.app_setting; чтение на каждый вызов (меняются администратором на лету)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _get(self, key: str) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM app.app_setting WHERE key = %s", (key,)).fetchone()
        return row[0] if row else None

    def deep_size(self) -> int:
        return _parse(self._get(KEY_DEEP), DEEP_SIZE_MIN, DEEP_SIZE_MAX, DEEP_SIZE_DEFAULT)

    def screen_batch(self) -> int:
        return _parse(self._get(KEY_SCREEN), SCREEN_BATCH_MIN, SCREEN_BATCH_MAX,
                      SCREEN_BATCH_DEFAULT)

    def set(self, deep_size: int | None = None, screen_batch: int | None = None) -> None:
        check(deep_size, screen_batch)
        with self._pool.connection() as conn, conn.transaction():
            for key, value in ((KEY_DEEP, deep_size), (KEY_SCREEN, screen_batch)):
                if value is not None:
                    conn.execute(
                        "INSERT INTO app.app_setting (key, value) VALUES (%s, %s) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                        "updated_at = now()", (key, str(value)))
