"""Точка расширения для Dev-18: действия после успешного входа.

Пароль передаётся только аргументом в памяти запроса; хук не должен его хранить и писать.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

log = logging.getLogger(__name__)

#: (login, password) -> None. Пока список пуст (сессия Jira подключается в KON-52).
AfterLoginHook = Callable[[str, str], None]


def run_after_login(hooks: Iterable[AfterLoginHook], login: str, password: str) -> None:
    """Вызвать хуки. Сбой хука не отменяет вход (ADR-008); в лог идёт только класс ошибки."""
    for hook in hooks:
        try:
            hook(login, password)
        except Exception as exc:  # noqa: BLE001 - вход важнее побочного шага
            log.warning("after_login hook: сбой %s", type(exc).__name__)
