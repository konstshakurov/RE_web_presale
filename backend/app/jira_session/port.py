"""Порт входа в Jira (KON-52, UC-005 в редакции A4, FR-029).

Приложение знает только этот модуль: ``login(username, password) -> токен`` и типизированные
отказы. Пароль живёт только в аргументе вызова; в исключения, repr и логи он не попадает.
Возвращаемый токен всегда запечатан (его печатает ``jira_client.TokenVault``): открытого
токена за пределами адаптера нет.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

#: Состояния, которые видит пользователь (``jira_status.state``).
STATE_OK = "ok"
STATE_BLOCKED = "blocked"  # Jira отказала: пароль, права, подтверждение входа
STATE_UNAVAILABLE = "unavailable"  # сеть, таймаут, 5xx, вход не включён: пароль не при чём
STATE_NOT_CHECKED = "not_checked"  # входа в Jira ещё не было в этой работе сервера

#: Коды причины (``jira_status.code``); по ним фронтенд различает «нет прав» и «недоступна».
CODE_AUTH_FAILED = "auth_failed"
CODE_LOGIN_BLOCKED = "login_blocked"
CODE_NO_PERMISSION = "no_permission"
CODE_JIRA_UNAVAILABLE = "jira_unavailable"
CODE_NOT_ENABLED = "not_enabled"
CODE_JIRA_ERROR = "jira_error"


class JiraSessionError(Exception):
    """Отказ входа в Jira. ``message`` — текст Jira как есть (без HTML и без пароля)."""

    def __init__(self, state: str, code: str, message: str = "") -> None:
        self.state = state
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class JiraNotEnabledError(JiraSessionError):
    """Вход в Jira не включён (нет JIRA_BASE_URL/ключа или выключен провайдер)."""

    def __init__(self, message: str = "Вход в Jira не включён") -> None:
        super().__init__(STATE_UNAVAILABLE, CODE_NOT_ENABLED, message)


@runtime_checkable
class JiraSessionPort(Protocol):
    def login(self, username: str, password: str) -> str:
        """Обменять логин и пароль на запечатанный токен сессии Jira; при отказе бросает
        :class:`JiraSessionError`."""
        ...
