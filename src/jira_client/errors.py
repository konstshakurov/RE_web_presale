# -*- coding: utf-8 -*-
"""Иерархия ошибок клиента Jira, отображающая коды ответов Jira в исключения.

Спроектировано по результатам пробы KON-30 (сырые ответы — в
``input/jira/probe_raw/probe_log.jsonl``). Ключевое наблюдение: Jira Server
8.13.0 отдаёт **разные тела для одного и того же кода 401**, поэтому парсер
обязан понимать обе формы.

Формы тел, зафиксированные пробой:

* ``{"message": "...", "status-code": 401}`` — нет аутентификации;
* ``{"errorMessages": ["У вас нет доступа для просмотра данного запроса.",
  "Необходимо ввести имя пользователя"], "errors": {}}`` — нет доступа к объекту;
* ``{"errorMessages": ["Вы не авторизованы. Для выполнения этой операции
  требуется аутентификация."], "errors": {}}`` — аутентифицирован, но прав
  не хватает;
* ``{"errorMessages": ["Ошибка доступа"], "errors": {}}`` — отказ аутентификации
  на ``/rest/auth/1/session``;
* ``{"errorMessages": ["Ошибка в запросе JQL: ..."], "errors": {}}`` — 400;
* ``{"errorMessages": ["ЗАПРОС НЕ СУЩЕСТВУЕТ"], "errors": {}}`` — 404;
* **HTML-страница на 13 КБ вместо JSON** — Jira отвечает так, если в запросе
  присутствует недействительная или пустая cookie. Зафиксировано пробой: в этом
  случае 401 получают даже те эндпоинты, которые анонимно отвечают 200.

Никакие секреты в исключения не попадают: тела обрезаются, заголовки запроса
не сохраняются.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

#: Сколько символов тела ответа хранить в исключении и в логе.
BODY_HEAD_LIMIT = 300

#: Что означает код 401 в терминах приложения. Причина важнее кода: FR-029
#: требует показать пользователю реальную причину, а FR-025 — запросить новую
#: авторизацию. Код 401 один, а причин несколько.
AUTH_REASON_NO_CREDENTIALS = "no_credentials"
AUTH_REASON_LOGIN_REQUIRED = "login_required"
AUTH_REASON_NO_PERMISSION = "no_permission"
AUTH_REASON_FAILED = "auth_failed"
AUTH_REASON_INVALID_COOKIE = "invalid_cookie_or_expired"
AUTH_REASON_UNKNOWN = "unknown"

#: Причины, после которых нужна новая авторизация пользователя (FR-029, FR-025).
REASONS_NEED_REAUTH = frozenset(
    {
        AUTH_REASON_NO_CREDENTIALS,
        AUTH_REASON_LOGIN_REQUIRED,
        AUTH_REASON_INVALID_COOKIE,
        AUTH_REASON_UNKNOWN,
    }
)

_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _html_summary(text: str) -> str:
    """Короткое описание HTML-отказа вместо самой страницы.

    Jira при недействительной cookie отдаёт HTML на 13 КБ. Показывать его
    пользователю бессмысленно, поэтому текст сжимается до заголовка страницы
    и сопровождается объяснением.
    """
    match = _HTML_TITLE_RE.search(text)
    title = " ".join(match.group(1).split()) if match else "без заголовка"
    return (
        "Jira вернула HTML-страницу отказа («%s») вместо JSON. "
        "Обычно это означает недействительную или истёкшую сессионную cookie."
        % title
    )


def classify_auth_failure(summary: str, *, is_json: bool = True) -> str:
    """Определить причину отказа по тексту тела ответа.

    Jira Server 8.13.0 отдаёт кодом 401 по меньшей мере четыре разных
    ситуации и дополнительно отвечает HTML-страницей при недействительной
    cookie. Различать их обязательно: «нет прав» и «сессия истекла» требуют от
    приложения противоположных действий.
    """
    if not is_json:
        return AUTH_REASON_INVALID_COOKIE
    lowered = (summary or "").lower()
    if "необходимо ввести имя пользователя" in lowered:
        return AUTH_REASON_LOGIN_REQUIRED
    if "вы не авторизованы" in lowered:
        return AUTH_REASON_NO_PERMISSION
    if "ошибка доступа" in lowered:
        return AUTH_REASON_FAILED
    if "must be authenticated" in lowered:
        return AUTH_REASON_NO_CREDENTIALS
    return AUTH_REASON_UNKNOWN


def needs_reauthorization(reason: str) -> bool:
    """Нужно ли требовать от пользователя повторную авторизацию."""
    return reason in REASONS_NEED_REAUTH


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def parse_error_body(body: bytes, content_type: str = "") -> Dict[str, Any]:
    """Разобрать тело ошибки Jira в структуру ``{summary, code, field_errors}``.

    Поддерживает формы, зафиксированные пробой, а также не-JSON ответы. Для
    HTML-отказа ``summary`` сжимается до одной строки, а исходный текст
    кладётся в ``raw_head`` — туда же он идёт в дамп пробы.
    """
    result: Dict[str, Any] = {
        "summary": "",
        "code": None,
        "field_errors": {},
        "is_json": False,
        "raw_head": "",
    }
    if not body:
        return result

    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return result

    looks_json = "json" in (content_type or "").lower() or text.startswith("{")
    if not looks_json:
        result["summary"] = _html_summary(text)
        result["raw_head"] = text[:BODY_HEAD_LIMIT]
        return result

    import json  # локальный импорт: не нужен, если ошибка не разбирается

    try:
        payload = json.loads(text)
    except ValueError:
        result["summary"] = _html_summary(text)
        result["raw_head"] = text[:BODY_HEAD_LIMIT]
        return result

    if not isinstance(payload, dict):
        result["summary"] = _html_summary(text)
        result["raw_head"] = text[:BODY_HEAD_LIMIT]
        return result

    result["is_json"] = True
    messages: List[str] = [
        _as_text(m) for m in (payload.get("errorMessages") or []) if _as_text(m)
    ]
    errors = payload.get("errors")
    if isinstance(errors, dict):
        field_errors = {str(k): _as_text(v) for k, v in errors.items() if _as_text(v)}
        for key, value in field_errors.items():
            messages.append("%s: %s" % (key, value))
    elif isinstance(errors, list):
        messages.extend(_as_text(e) for e in errors if _as_text(e))

    single = _as_text(payload.get("message"))
    if single:
        messages.insert(0, single)

    result["field_errors"] = field_errors if isinstance(errors, dict) else {}
    result["code"] = payload.get("status-code")
    result["summary"] = " | ".join(messages)[:BODY_HEAD_LIMIT]
    if not result["summary"]:
        result["summary"] = text[:BODY_HEAD_LIMIT]
    return result


class JiraError(Exception):
    """Базовая ошибка клиента Jira. ``retryable`` управляет повторами."""

    #: HTTP-код, для которого поднимается этот класс (``None`` — сетевой сбой).
    status: Optional[int] = None
    #: Повторять ли запрос. Истечение сессии и «нет прав» повтором не чинятся.
    retryable: bool = False

    def __init__(
        self,
        summary: str = "",
        *,
        status: Optional[int] = None,
        code: Any = None,
        field_errors: Optional[Mapping[str, str]] = None,
        body_head: str = "",
        is_json: bool = True,
        method: str = "",
        path: str = "",
        reason: Optional[str] = None,
    ) -> None:
        self.summary = summary
        self.status = status
        self.code = code
        self.field_errors: Dict[str, str] = dict(field_errors or {})
        self.body_head = body_head
        self.is_json = is_json
        self.method = method
        self.path = path
        #: Для 401 — какая именно ситуация (см. ``classify_auth_failure``).
        self.reason = reason or (
            classify_auth_failure(summary, is_json=is_json) if status == 401 else None
        )
        super().__init__(self._render())

    def _render(self) -> str:
        head = "%s %s" % (self.method, self.path) if self.path else ""
        head = "%s -> %s" % (head, self.status) if self.status else head
        tail = ": %s" % self.summary if self.summary else ""
        return ("%s %s" % (head, tail)).strip()

    @classmethod
    def from_response(cls, response, *, method: str = "", path: str = "") -> "JiraError":
        parsed = parse_error_body(
            response.content, response.headers.get("content-type", "")
        )
        return cls(
            parsed["summary"],
            status=response.status_code,
            code=parsed["code"],
            field_errors=parsed["field_errors"],
            body_head=parsed["raw_head"],
            is_json=parsed["is_json"],
            method=method,
            path=path,
        )


class JiraAuthError(JiraError):
    """401: сессия отсутствует, истекла или отвергнута.

    Истёкшая сессия наступает без предупреждения — это основание для
    FR-029 / ISS-013: клиент обязан отличить её от «нет прав». Причина
    доступна в ``reason``; свойство ``needs_reauthorization`` скажет, просить
    ли пользователя войти заново (FR-025).
    """

    @property
    def needs_reauthorization(self) -> bool:
        return needs_reauthorization(self.reason or AUTH_REASON_UNKNOWN)


class JiraPermissionError(JiraError):
    """403, либо 401 с текстом «Вы не авторизованы…».

    Jira Server 8.13.0 отдаёт такой отказ кодом 401, поэтому клиент сверяется
    с телом, а не только с кодом. FR-026: права определяются правами
    пользователя в Jira, отказ показывается как есть — подменять его нельзя.
    """


class JiraNotFoundError(JiraError):
    """404: задачи, поля или ресурса нет."""


class JiraBadRequestError(JiraError):
    """400: некорректный JQL, неверное тело или неизвестное поле.

    ``field_errors`` заполняется, если Jira вернула ``errors``.
    """


class JiraConflictError(JiraError):
    """409: конфликт версий или состояния."""


class JiraRateLimitedError(JiraError):
    """429 либо 503 с признаком перегрузки. Единственная ошибка, где повтор оправдан."""

    status = 429
    retryable = True


class JiraServerError(JiraError):
    """5xx: сбой на стороне Jira."""


class JiraTimeoutError(JiraError):
    """Превышен лимит 10 с на вызов (NFR-009)."""

    retryable = True

    def __init__(self, summary: str = "", *, timeout: Optional[float] = None, **kw) -> None:
        self.timeout = timeout
        super().__init__(summary, **kw)


class JiraUnavailableError(JiraError):
    """Сеть недоступна, TLS не прошёл, соединение оборвалось. Повтор оправдан."""

    retryable = True


#: Таблица соответствия кодов ответа исключениям клиента.
STATUS_MAP: Dict[int, type] = {
    400: JiraBadRequestError,
    401: JiraAuthError,
    403: JiraPermissionError,
    404: JiraNotFoundError,
    409: JiraConflictError,
    429: JiraRateLimitedError,
}


def error_for_response(response, *, method: str = "", path: str = "") -> JiraError:
    """Выбрать класс исключения по коду ответа Jira.

    Код 401 разбирается по телу: Jira отдаёт им и «нет прав», и «сессия
    истекла», а это противоположные действия для приложения.
    """
    status = response.status_code
    if status == 401:
        parsed = parse_error_body(response.content, response.headers.get("content-type", ""))
        reason = classify_auth_failure(parsed["summary"], is_json=parsed["is_json"])
        if reason == AUTH_REASON_NO_PERMISSION:
            return JiraPermissionError.from_response(response, method=method, path=path)
        return JiraAuthError.from_response(response, method=method, path=path)
    if status in STATUS_MAP:
        return STATUS_MAP[status].from_response(response, method=method, path=path)
    if status >= 500:
        cls = JiraRateLimitedError if status == 503 else JiraServerError
        return cls.from_response(response, method=method, path=path)
    return JiraError.from_response(response, method=method, path=path)