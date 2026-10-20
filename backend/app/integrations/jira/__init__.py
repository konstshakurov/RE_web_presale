# -*- coding: utf-8 -*-
"""Интеграция приложения с Jira (Dev-18, KON-34).

Единственная точка входа приложения::

    from app.integrations.jira import JiraAdapter

    with JiraAdapter() as jira:
        session = jira.login(username, password)
        check = jira.check_session(session.sealed_token)

Состав:

* :mod:`.port` — ``JiraPort`` и типы результатов: о них знает приложение;
* :mod:`.errors` — единая карта ошибок (``needs_reauthorization``,
  ``no_permission``, ``auth_failed``, ``login_blocked``, ``jira_unavailable``);
* :mod:`.adapter` — реализация порта поверх ``src/jira_client``.

За пределами ``integrations`` приложение ``jira_client`` не импортирует.
"""

from .adapter import JiraAdapter
from .errors import (
    CODE_AUTH_FAILED,
    CODE_JIRA_ERROR,
    CODE_JIRA_UNAVAILABLE,
    CODE_LOGIN_BLOCKED,
    CODE_NEEDS_REAUTHORIZATION,
    CODE_NO_PERMISSION,
    OPERATIONAL_CODES,
    JiraIntegrationError,
    classify,
    code_of,
)
from .port import (
    SESSION_JIRA_UNAVAILABLE,
    SESSION_NEEDS_REAUTHORIZATION,
    SESSION_NO_PERMISSION,
    SESSION_OK,
    SESSION_STATES,
    RATE_PROPOSER_PRODUCTION,
    RATE_PROPOSER_CLIENT,
    RATE_VAT_YES,
    RATE_VAT_NO,
    RATE_CURRENCY_RUB,
    RATE_CURRENCY_EUR,
    RATE_CURRENCY_GBR,
    RATE_CURRENCY_KZT,
    RATE_CURRENCY_USD,
    RATE_UNIT_HOUR,
    RATE_VAT_IDS,
    RATE_CURRENCY_IDS,
    RATE_UNIT_IDS,
    AllowedValue,
    JiraPort,
    SealedSession,
    SessionCheck,
    TransitionField,
    TransitionView,
)

__all__ = [
    "RATE_PROPOSER_PRODUCTION",
    "RATE_PROPOSER_CLIENT",
    "RATE_VAT_YES",
    "RATE_VAT_NO",
    "RATE_CURRENCY_RUB",
    "RATE_CURRENCY_EUR",
    "RATE_CURRENCY_GBR",
    "RATE_CURRENCY_KZT",
    "RATE_CURRENCY_USD",
    "RATE_UNIT_HOUR",
    "RATE_VAT_IDS",
    "RATE_CURRENCY_IDS",
    "RATE_UNIT_IDS",
    "JiraAdapter",
    "JiraPort",
    "SealedSession",
    "SessionCheck",
    "AllowedValue",
    "TransitionField",
    "TransitionView",
    "JiraIntegrationError",
    "classify",
    "code_of",
    "SESSION_OK",
    "SESSION_NEEDS_REAUTHORIZATION",
    "SESSION_JIRA_UNAVAILABLE",
    "SESSION_NO_PERMISSION",
    "SESSION_STATES",
    "CODE_NEEDS_REAUTHORIZATION",
    "CODE_NO_PERMISSION",
    "CODE_AUTH_FAILED",
    "CODE_LOGIN_BLOCKED",
    "CODE_JIRA_UNAVAILABLE",
    "CODE_JIRA_ERROR",
    "OPERATIONAL_CODES",
]
