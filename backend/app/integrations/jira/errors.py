# -*- coding: utf-8 -*-
"""Единая карта ошибок интеграции Jira (Dev-18, KON-34).

Приложение не разбирает классы исключений ``jira_client`` и не смотрит на
коды HTTP. Все отказы сводятся к одному признаку ``code``:

======================  ===================================================
``code``                Что делать приложению
======================  ===================================================
``needs_reauthorization`` 401 ``login_required`` / ``invalid_cookie_or_expired``
                     (и прочие «сессии нет») — спросить пароль заново
``no_permission``      прав нет: показать отказ, повторный вход не поможет
``auth_failed``        вход отклонён: неверный логин или пароль
``login_blocked``      Jira требует подтвердить вход в браузере (CAPTCHA,
                     блокировка) — повтор по REST не поможет
``jira_unavailable``   сеть, TLS, таймаут, 5xx — повторить позже
``jira_error``         400/404/409 и прочее: показать сообщение Jira как есть
======================  ===================================================

«Jira недоступна» и «сессия истекла» не смешиваются: первое лечится
ожиданием, второе — повторным вводом пароля (критерий приёмки Dev-18).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from jira_client import (
    AUTH_REASON_FAILED,
    LOGIN_REASON_BLOCKED,
    CryptoError,
    JiraAuthError,
    JiraBadRequestError,
    JiraError,
    JiraLoginError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitedError,
    JiraServerError,
    JiraTimeoutError,
    JiraUnavailableError,
    needs_reauthorization,
)

#: 401 «сессии нет» — нужен повторный ввод пароля.
CODE_NEEDS_REAUTHORIZATION = "needs_reauthorization"
#: Отказ по правам. Повторный вход и ожидание не помогут.
CODE_NO_PERMISSION = "no_permission"
#: Неверный логин или пароль при входе.
CODE_AUTH_FAILED = "auth_failed"
#: Jira просит подтвердить вход через браузер (CAPTCHA, блокировка).
CODE_LOGIN_BLOCKED = "login_blocked"
#: Jira недоступна: сеть, TLS, таймаут, 5xx.
CODE_JIRA_UNAVAILABLE = "jira_unavailable"
#: Не из операционной карты: 400/404/409 и неизвестное. Показывается как есть.
CODE_JIRA_ERROR = "jira_error"

#: Пять операционных признаков из карты Dev-18. ``jira_error`` — остаточный.
OPERATIONAL_CODES = (
    CODE_NEEDS_REAUTHORIZATION,
    CODE_NO_PERMISSION,
    CODE_AUTH_FAILED,
    CODE_LOGIN_BLOCKED,
    CODE_JIRA_UNAVAILABLE,
)


class JiraIntegrationError(Exception):
    """Ошибка интеграции Jira с единым признаком :attr:`code`."""

    #: Значение по умолчанию; :func:`classify` проставляет явно.
    code: str = CODE_JIRA_ERROR

    def __init__(
        self,
        message: str = "",
        *,
        code: Optional[str] = None,
        reason: Optional[str] = None,
        status: Optional[int] = None,
        retryable: bool = False,
    ) -> None:
        if code:
            self.code = code
        #: Что именно сказала Jira (``no_credentials``, ``login_required`` …).
        self.reason = reason or ""
        #: HTTP-код, если он был.
        self.status = status
        #: Можно ли повторить запрос без вмешательства пользователя.
        self.retryable = bool(retryable)
        self.message = message or self.code
        super().__init__("[%s] %s" % (self.code, self.message))

    @property
    def needs_reauthorization(self) -> bool:
        return self.code == CODE_NEEDS_REAUTHORIZATION

    @property
    def jira_unavailable(self) -> bool:
        return self.code == CODE_JIRA_UNAVAILABLE

    def describe(self) -> Dict[str, Any]:
        """Безопасное описание для лога."""
        return {
            "code": self.code,
            "reason": self.reason,
            "status": self.status,
            "retryable": self.retryable,
        }


def classify(exc: BaseException) -> JiraIntegrationError:
    """Свести исключение ``jira_client`` к единой карте ошибок.

    ``login`` порождает ``auth_failed`` / ``login_blocked`` /``jira_unavailable``;
    чтение — ``needs_reauthorization`` / ``no_permission`` / ``jira_unavailable``.
    """
    if isinstance(exc, JiraIntegrationError):
        return exc

    if isinstance(exc, JiraLoginError):
        code = CODE_LOGIN_BLOCKED if exc.login_reason == LOGIN_REASON_BLOCKED else CODE_AUTH_FAILED
        # hint не содержит HTML-страницы Jira, которую пользователю показывать нельзя.
        return JiraIntegrationError(
            exc.hint or str(exc),
            code=code,
            reason=exc.reason,
            status=exc.status,
        )

    if isinstance(exc, JiraPermissionError):
        return JiraIntegrationError(
            str(exc) or "Jira отклонила запрос: недостаточно прав",
            code=CODE_NO_PERMISSION,
            reason=exc.reason,
            status=exc.status,
        )

    if isinstance(exc, JiraTimeoutError):
        # Лимит вызова - NFR-009. str(exc) у таймаута начинается с «: » (пустые метод и путь)
        # и содержит внутренний ID требования, поэтому текст для пользователя строим сами (KON-119).
        limit = f"{exc.timeout:.0f}" if exc.timeout else "10"
        return JiraIntegrationError(
            f"Превышено время ожидания ответа Jira ({limit} с).",
            code=CODE_JIRA_UNAVAILABLE,
            status=exc.status,
            retryable=True,
        )

    if isinstance(exc, JiraUnavailableError):
        return JiraIntegrationError(
            str(exc) or "Jira не отвечает",
            code=CODE_JIRA_UNAVAILABLE,
            status=exc.status,
            retryable=True,
        )

    if isinstance(exc, (JiraServerError, JiraRateLimitedError)):
        return JiraIntegrationError(
            str(exc) or "Jira вернула серверную ошибку",
            code=CODE_JIRA_UNAVAILABLE,
            status=exc.status,
            retryable=True,
        )

    if isinstance(exc, JiraAuthError):
        # 401 при чтении: сессии нет либо она истекла. Повторный вход поможет,
        # поэтому это не «недоступна».
        if needs_reauthorization(exc.reason or "") or exc.reason == AUTH_REASON_FAILED:
            return JiraIntegrationError(
                str(exc) or "Сессия Jira истекла",
                code=CODE_NEEDS_REAUTHORIZATION,
                reason=exc.reason,
                status=exc.status,
            )
        return JiraIntegrationError(
            str(exc), code=CODE_JIRA_ERROR, reason=exc.reason, status=exc.status
        )

    if isinstance(exc, CryptoError):
        # Запечатанный токен не распечатывается — сессия утрачена.
        return JiraIntegrationError(
            "Сессионный токен не читается: требуется повторный вход",
            code=CODE_NEEDS_REAUTHORIZATION,
        )

    if isinstance(exc, JiraNotFoundError):
        return JiraIntegrationError(
            str(exc) or "Объект не найден в Jira",
            code=CODE_JIRA_ERROR,
            status=exc.status,
        )

    if isinstance(exc, JiraBadRequestError):
        return JiraIntegrationError(
            str(exc) or "Jira отклонила запрос",
            code=CODE_JIRA_ERROR,
            status=exc.status,
        )

    if isinstance(exc, JiraError):
        return JiraIntegrationError(
            str(exc) or "Ошибка Jira", code=CODE_JIRA_ERROR, status=exc.status
        )

    return JiraIntegrationError(str(exc) or type(exc).__name__, code=CODE_JIRA_ERROR)


def code_of(exc: BaseException) -> str:
    """Признак ошибки без выбрасывания исключения (для логов и метрик)."""
    return classify(exc).code


__all__ = [
    "JiraIntegrationError",
    "classify",
    "code_of",
    "CODE_NEEDS_REAUTHORIZATION",
    "CODE_NO_PERMISSION",
    "CODE_AUTH_FAILED",
    "CODE_LOGIN_BLOCKED",
    "CODE_JIRA_UNAVAILABLE",
    "CODE_JIRA_ERROR",
    "OPERATIONAL_CODES",
]
