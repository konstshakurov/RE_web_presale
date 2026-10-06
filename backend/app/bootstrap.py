"""Начальное заполнение: админы из ADMIN_LOGINS (идемпотентно, при старте api)."""
from __future__ import annotations

import logging

from .app_repo import AppRepo

log = logging.getLogger(__name__)

_MAX_LOGIN = 128


def seed_admins(repo: AppRepo, logins: tuple[str, ...]) -> int:
    """Создать недостающих админов (role=admin, active=true). Существующие строки не меняются.

    Возвращает число созданных. Логин длиннее 128 пропускается (ограничение схемы).
    """
    created = 0
    for login in logins:
        if not 1 <= len(login) <= _MAX_LOGIN:
            log.error("ADMIN_LOGINS: логин пропущен, длина вне 1..%d", _MAX_LOGIN)
            continue
        if repo.ensure_admin(login):
            created += 1
    log.info("seed админов: создано %d из %d", created, len(logins))
    return created
