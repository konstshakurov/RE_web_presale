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


# ---------------------------------------------------------------------------
# Отказ входа: POST /rest/auth/1/session (Dev-18)
# ---------------------------------------------------------------------------
#
# Это **отдельный** набор причин от ``AUTH_REASON_*``: вход требует пароля, и
# приложение обязано различать три несводимые ситуации — «неверный пароль»
# (показать форму заново), «Jira просит подтвердить вход в браузере»
# (повтор по REST не поможет) и «Jira недоступна» (повторить позже).
# Смешивание третьей со второй привело бы к тому, что недоступная Jira
# выглядела бы как неверный пароль.

#: Неверный логин или пароль (401, ``x-seraph-loginreason: AUTHENTICATED_FAILED``).
LOGIN_REASON_AUTH_FAILED = "auth_failed"
#: CAPTCHA либо блокировка: Jira требует ввести данные через браузер
#: (``x-seraph-loginreason: AUTHENTICATION_DENIED``).
LOGIN_REASON_BLOCKED = "login_blocked"
#: Сетевой сбой, TLS, таймаут: пароль не проверялся, повторять бессмысленно.
LOGIN_REASON_UNAVAILABLE = "jira_unavailable"

#: Заголовок, которым Jira Server объясняет отказ входа.
SERAPH_LOGIN_REASON_HEADER = "x-seraph-loginreason"
#: Значение заголовка при неверном логине или пароле.
SERAPH_AUTH_FAILED = "AUTHENTICATED_FAILED"
#: Значения заголовка при отказе в доступе к самому входу (CAPTCHA, блокировка).
SERAPH_AUTHENTICATION_DENIED = "AUTHENTICATION_DENIED"

#: Причины отказа входа, которые вообще могут прийти из ``classify_login_failure``.
LOGIN_REASONS = frozenset({LOGIN_REASON_AUTH_FAILED, LOGIN_REASON_BLOCKED})

#: Текст для пользователя. HTML-страницу Jira показывать нельзя (Dev-18),
#: поэтому отказ переводится в короткое объяснение.
LOGIN_HINTS: Dict[str, str] = {
    LOGIN_REASON_AUTH_FAILED: "Неверный логин или пароль Jira.",
    LOGIN_REASON_BLOCKED: (
        "Jira требует подтвердить вход через браузер (CAPTCHA или блокировка). "
        "Повторный запрос по REST в такой ситуации не поможет."
    ),
}


def classify_login_failure(response) -> str:
    """Причина отказа ``POST /rest/auth/1/session``.

    Признаки, зафиксированные пробой KON-30 на живой Jira 8.13.0:

    * ``x-seraph-loginreason: AUTHENTICATED_FAILED`` — неверный логин или пароль;
    * ``x-seraph-loginreason: AUTHENTICATION_DENIED`` — Jira просит пройти
      проверку через браузер; REST-повтором это не лечится.

    Если заголовка нет, причина берётся из тела ответа, но «Вы не
    авторизованы» трактуется как отказ в доступе к входу, а не как неверный
    пароль.

    Сетевые ошибки сюда не попадают: ``httpx`` их превращает в
    ``JiraTimeoutError`` / ``JiraUnavailableError``, и они обязаны
    различаться с «неверным паролём» (Dev-18).
    """
    header = (response.headers.get(SERAPH_LOGIN_REASON_HEADER) or "").strip().upper()
    if header == SERAPH_AUTHENTICATION_DENIED:
        return LOGIN_REASON_BLOCKED
    if header == SERAPH_AUTH_FAILED:
        return LOGIN_REASON_AUTH_FAILED
    if response.status_code in (401, 403):
        parsed = parse_error_body(response.content, response.headers.get("content-type", ""))
        reason = classify_auth_failure(parsed["summary"], is_json=parsed["is_json"])
        if reason == AUTH_REASON_NO_PERMISSION:
            return LOGIN_REASON_BLOCKED
    return LOGIN_REASON_AUTH_FAILED


#: Сколько знаков текста Jira показывается пользователю при отказе входа (FR-029).
USER_TEXT_LIMIT = 500
#: Чем заменяется эхо секрета (пароля) в тексте для пользователя.
SECRET_MASK = "[скрыто]"

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_TAG_RE = re.compile(r"<[^>]*>")


def mask_secrets(text: str, secrets=()) -> str:
    """Заменить вхождения секретов (пароль, эхо в ответе Jira) на ``[скрыто]``."""
    for secret in secrets or ():
        if secret:
            text = text.replace(secret, SECRET_MASK)
    return text


def extract_user_text(body: bytes, content_type: str = "", secrets=()) -> str:
    """Текст отказа Jira «как есть» для показа пользователю (FR-029).

    Возвращает строку только если тело — JSON-объект с ``errorMessages`` /
    ``errors`` (либо ``message``). HTML, не-JSON и пустое тело дают ``""``:
    вызывающий подставляет обобщённый hint. Заголовки и cookie сюда не
    попадают вовсе; управляющие символы и теги убираются, пароль из ``secrets``
    заменяется на ``[скрыто]``, длина ограничена ``USER_TEXT_LIMIT``.
    """
    if not body:
        return ""
    text = body.decode("utf-8", errors="replace").strip()
    if not text.startswith("{"):
        return ""
    import json

    try:
        payload = json.loads(text)
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""

    parts: List[str] = []
    single = _as_text(payload.get("message"))
    if single:
        parts.append(single)
    messages = payload.get("errorMessages")
    if isinstance(messages, list):
        parts.extend(_as_text(m) for m in messages if _as_text(m))
    errors = payload.get("errors")
    if isinstance(errors, dict):
        parts.extend("%s: %s" % (k, _as_text(v)) for k, v in errors.items() if _as_text(v))
    elif isinstance(errors, list):
        parts.extend(_as_text(e) for e in errors if _as_text(e))

    joined = mask_secrets(" ".join(parts), secrets)
    joined = _TAG_RE.sub(" ", joined)
    joined = " ".join(_CONTROL_RE.sub(" ", joined).split())
    return joined[:USER_TEXT_LIMIT]


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


class JiraLoginError(JiraAuthError):
    """Отказ ``POST /rest/auth/1/session``: вход не выполнен.

    ``login_reason`` — ``auth_failed`` (неверный логин или пароль) либо
    ``login_blocked`` (Jira требует подтвердить вход в браузере). Отказ
    ``jira_unavailable`` сюда не попадает: сеть, TLS и таймаут дают
    ``JiraUnavailableError`` / ``JiraTimeoutError`` и путать их с неверным
    паролём нельзя (Dev-18).

    Тело запроса, в котором находится пароль, в исключение не попадает:
    ``summary`` и ``body_head`` берутся только из **ответа** Jira.
    """

    def __init__(
        self,
        summary: str = "",
        *,
        login_reason: str = LOGIN_REASON_AUTH_FAILED,
        user_text: str = "",
        **kw: Any,
    ) -> None:
        self.login_reason = login_reason
        #: Текст отказа из ответа Jira (FR-029); пусто — тело не JSON.
        self.user_text = user_text or ""
        kw.setdefault("reason", login_reason)
        super().__init__(summary, **kw)

    @property
    def hint(self) -> str:
        """Текст для пользователя: текст из JSON-ответа Jira как есть (FR-029),
        иначе обобщённое объяснение. HTML-страница Jira не показывается."""
        return self.user_text or LOGIN_HINTS.get(self.login_reason, "")

    def _render(self) -> str:
        base = super()._render()
        hint = LOGIN_HINTS.get(self.login_reason, "")
        return ("%s %s" % (base, hint)).strip() if hint else base


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