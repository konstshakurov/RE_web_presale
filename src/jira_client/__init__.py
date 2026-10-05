# -*- coding: utf-8 -*-
"""Клиент Jira для re_web_presale (задача KON-30).

Назначение: чтение и запись в Jira Server 8.13.0 от имени пользователя без
технической учётной записи и без Application Link. Реализовано только то, что
подтверждено пробой; обоснование и открытые вопросы — в
``architecture/probes/KON-30_jira_probe_report.md``.

Быстрый старт::

    from jira_client import JiraClient

    with JiraClient() as client:
        print(client.server_info())
        for demand in client.search_paged('project = ANALYTICS AND updated > "2026-10-05 09:00"'):
            ...

Конфигурация — только из переменных окружения, шаблон в ``.env.example``.
Секреты в коде отсутствуют и не попадают в логи: у стратегий аутентификации
переопределён ``__repr__``, а ``JiraConfig.safe_summary()`` отдаёт только
признаки «задано / не задано».
"""

from .auth import (
    AnonymousAuth,
    AuthStrategy,
    BasicAuth,
    SessionLoginProvider,
    SessionTokenAuth,
    build_auth,
)
from .client import JiraClient, probe_client
from .config import ConfigError, JiraConfig, load_dotenv
from .crypto import CryptoError, TokenVault, generate_key
from .errors import (
    AUTH_REASON_FAILED,
    AUTH_REASON_INVALID_COOKIE,
    AUTH_REASON_LOGIN_REQUIRED,
    AUTH_REASON_NO_CREDENTIALS,
    AUTH_REASON_NO_PERMISSION,
    AUTH_REASON_UNKNOWN,
    JiraAuthError,
    JiraBadRequestError,
    JiraConflictError,
    JiraError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitedError,
    JiraServerError,
    JiraTimeoutError,
    JiraUnavailableError,
    classify_auth_failure,
    error_for_response,
    needs_reauthorization,
    parse_error_body,
)
from .models import (
    Attachment,
    Comment,
    Demand,
    FieldSpec,
    Person,
    Reference,
    SearchPage,
    ServerInfo,
    Transition,
)

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # клиент
    "JiraClient",
    "probe_client",
    # конфигурация
    "JiraConfig",
    "ConfigError",
    "load_dotenv",
    # аутентификация
    "AuthStrategy",
    "AnonymousAuth",
    "SessionTokenAuth",
    "BasicAuth",
    "SessionLoginProvider",
    "build_auth",
    # шифрование
    "TokenVault",
    "CryptoError",
    "generate_key",
    # ошибки
    "JiraError",
    "JiraAuthError",
    "JiraPermissionError",
    "JiraNotFoundError",
    "JiraBadRequestError",
    "JiraConflictError",
    "JiraRateLimitedError",
    "JiraServerError",
    "JiraTimeoutError",
    "JiraUnavailableError",
    "parse_error_body",
    "error_for_response",
    "classify_auth_failure",
    "needs_reauthorization",
    "AUTH_REASON_NO_CREDENTIALS",
    "AUTH_REASON_LOGIN_REQUIRED",
    "AUTH_REASON_NO_PERMISSION",
    "AUTH_REASON_FAILED",
    "AUTH_REASON_INVALID_COOKIE",
    "AUTH_REASON_UNKNOWN",
    # модели
    "Attachment",
    "Comment",
    "Demand",
    "FieldSpec",
    "Person",
    "Reference",
    "SearchPage",
    "ServerInfo",
    "Transition",
]