"""Реализации JiraSessionPort: над ``integrations.jira`` и заглушки без Jira."""
from __future__ import annotations

from .port import (
    CODE_AUTH_FAILED,
    CODE_JIRA_ERROR,
    CODE_JIRA_UNAVAILABLE,
    CODE_LOGIN_BLOCKED,
    CODE_NO_PERMISSION,
    STATE_BLOCKED,
    STATE_UNAVAILABLE,
    JiraNotEnabledError,
    JiraSessionError,
)

# Коды integrations.jira (строки карты ошибок) -> (состояние, код). integrations.jira тянет
# jira_client (src/), которого может не быть в образе: импорт только внутри login().
_MAP = {
    "auth_failed": (STATE_BLOCKED, CODE_AUTH_FAILED),
    "login_blocked": (STATE_BLOCKED, CODE_LOGIN_BLOCKED),
    "no_permission": (STATE_BLOCKED, CODE_NO_PERMISSION),
    "jira_unavailable": (STATE_UNAVAILABLE, CODE_JIRA_UNAVAILABLE),
}


class JiraClientSessionAdapter:
    """Вход через ``JiraPort.login`` (адаптер над ``jira_client.SessionLoginProvider``)."""

    def __init__(self, jira) -> None:  # JiraPort
        self._jira = jira

    def login(self, username: str, password: str) -> str:
        from ..integrations.jira import classify

        try:
            return self._jira.login(username, password).sealed_token
        except JiraSessionError:
            raise
        except Exception as exc:  # noqa: BLE001 - любой сбой переводим в типизированный
            err = classify(exc)
            state, code = _MAP.get(err.code, (STATE_BLOCKED, CODE_JIRA_ERROR))
            # from None: цепочка исключений не тянет за собой контекст вызова с паролем
            raise JiraSessionError(state, code, err.message) from None


class DisabledJiraSession:
    """Стенд без JIRA_BASE_URL: вход в приложение работает, вход в Jira «не включён»."""

    def login(self, username: str, password: str) -> str:
        raise JiraNotEnabledError()
