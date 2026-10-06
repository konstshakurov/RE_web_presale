"""Пул соединений к app_db. Строка подключения только из env (config.py)."""
from __future__ import annotations

from psycopg_pool import ConnectionPool


def create_pool(dsn: str, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    # open=False: соединение не создаётся при импорте, вызывающий делает pool.open()
    return ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False)
