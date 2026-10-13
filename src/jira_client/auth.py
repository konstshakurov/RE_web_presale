# -*- coding: utf-8 -*-
"""Стратегии аутентификации в Jira Server 8.13.0.

Проба KON-30 ограничена одним жёстким фактом: PAT в Jira Server появился в
8.14, а здесь 8.13.0 — токена пользователя через REST не существует.
Поэтому доступно ровно три вещи:

``SessionTokenAuth``
    Заголовок ``Cookie: <имя>=<токен>``. Это **сессионный токен**, а не пароль:
    его можно шифровать и хранить (NFR-006). Основной режим клиента.
``BasicAuth``
    ``Authorization: Basic``. Требует пароль. Применяется только для
    сервисной/диагностической пробы: владелец решил, что техническая учётка
    проекту не нужна.
``SessionLoginProvider``
    ``POST /rest/auth/1/session`` с телом ``{"username": ..., "password": ...}``
    — обмен паролем на сессионную cookie. Включён решением владельца 05.10
    (ADR-005, Dev-18): логин и пароль Jira совпадают с LDAP, поэтому один ввод
    пароля при входе в приложение используется и для создания сессии Jira.

**Пароль не сохраняется.** Он существует только как тело одного HTTP-запроса:
не пишется в атрибуты, не попадает в ``repr``, в логи, в ``last_call``, в
исключения и в трассировку. Хранится только сессионный токен, и приложение
держит его зашифрованным (``TokenVault``, NFR-006).

Секреты никуда не логируются: у всех стратегий есть безопасное описание.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

import httpx

from .config import DEFAULT_SESSION_COOKIE, DEFAULT_TIMEOUT
from .errors import (
    JiraError,
    JiraLoginError,
    JiraTimeoutError,
    JiraUnavailableError,
    classify_login_failure,
    error_for_response,
    extract_user_text,
    mask_secrets,
    parse_error_body,
)

#: Эндпоинт создания сессии. Форма тела подтверждена пробой KON-30.
SESSION_LOGIN_ENDPOINT = "/rest/auth/1/session"

#: Форма тела: плоская. Вложенная форма ``{"session": {...}}`` Jira 8.13.0
#: отклоняет: 400 «Unrecognized field "session" (Class
#: com.atlassian.jira.rest.auth.AuthParams)».
SESSION_LOGIN_BODY_FIELDS = ("username", "password")

#: Единый User-Agent: в логах Jira видно, кто именно выполнил запрос.
USER_AGENT = "re_web_presale-jira-client/1.0"


class AuthStrategy:
    """Общий интерфейс: подготовить заголовки запроса."""

    #: Человекочитаемое имя для логов и отчётов.
    name = "anonymous"

    def headers(self) -> Dict[str, str]:
        return {}

    def describe(self) -> Dict[str, Any]:
        return {"mode": self.name}


class AnonymousAuth(AuthStrategy):
    name = "anonymous"


class SessionTokenAuth(AuthStrategy):
    """Аутентификация сессионной cookie.

    Токен — секрет, поэтому ``__repr__`` и ``describe`` его не показывают.
    """

    name = "session_cookie"

    def __init__(self, token: str, cookie_name: str = "JSESSIONID") -> None:
        if not token:
            raise ValueError("Пустой сессионный токен")
        self._cookie_name = cookie_name or "JSESSIONID"
        self._token = token
        self._cookie_header = "%s=%s" % (self._cookie_name, token)

    def headers(self) -> Dict[str, str]:
        return {"Cookie": self._cookie_header}

    def describe(self) -> Dict[str, Any]:
        return {"mode": self.name, "cookie": self._cookie_name, "token_set": True}

    def __repr__(self) -> str:  # pragma: no cover - защита от утечки в лог
        return "<SessionTokenAuth cookie=%s token=***>" % self._cookie_name


class BasicAuth(AuthStrategy):
    """HTTP Basic. Требует пароль — только для пробы или технической учётки."""

    name = "basic"

    def __init__(self, username: str, password: str) -> None:
        if not username or not password:
            raise ValueError("Basic требует и логин, и пароль")
        self._username = username
        self._raw = "%s:%s" % (username, password)
        self._header = "Basic " + base64.b64encode(self._raw.encode("utf-8")).decode("ascii")

    def headers(self) -> Dict[str, str]:
        return {"Authorization": self._header}

    def describe(self) -> Dict[str, Any]:
        return {"mode": self.name, "username_set": True, "password_set": True}

    def __repr__(self) -> str:  # pragma: no cover - защита от утечки в лог
        return "<BasicAuth username_set=True password=***>"


@dataclass(frozen=True, repr=False)
class SessionToken:
    """Результат входа: имя cookie и её значение.

    ``token`` — секрет (NFR-006): он не попадает в ``repr``, в ``str``, в
    ``describe`` и в логи. Наружу приложение получает его только зашифрованным
    (``TokenVault``).
    """

    #: Имя cookie, которое Jira установила в ответе (``JSESSIONID`` по умолчанию).
    cookie_name: str
    #: Значение cookie — то, что уходит в заголовок ``Cookie``. Секрет.
    token: str
    #: Когда сессия создана (UTC). Нужно Dev-18 для оценки времени жизни.
    created_at: datetime
    #: Логин, которому принадлежит сессия. Никогда не выводится в логи.
    username: str = ""

    def cookie_header_value(self) -> str:
        """Значение заголовка ``Cookie`` — используется только внутри клиента."""
        return "%s=%s" % (self.cookie_name, self.token)

    def describe(self) -> Dict[str, Any]:
        """Безопасное описание для логов и отчётов: без значения токена."""
        return {
            "mode": "session_cookie",
            "cookie": self.cookie_name,
            "token_set": bool(self.token),
            "token_length": len(self.token),
            "username_set": bool(self.username),
            "created_at": self.created_at.isoformat(timespec="seconds"),
        }

    def __repr__(self) -> str:
        return "<SessionToken cookie=%s token=*** created_at=%s>" % (
            self.cookie_name,
            self.created_at.isoformat(timespec="seconds"),
        )


class SessionLoginProvider:
    """Обмен логина и пароля на сессионную cookie (ADR-005, Dev-18).

    ``login(username, password)`` выполняет ровно один запрос
    ``POST /rest/auth/1/session`` и возвращает :class:`SessionToken`.

    Повторов нет, лимит — тот же, что у всего клиента (10 с, NFR-009).

    **Пароль не сохраняется.** Он передаётся только в теле запроса и
    удаляется сразу после отправки; в атрибутах объекта, в ``describe``,
    в исключениях и в ``last_call`` его нет.
    """

    #: Путь эндпоинта. Меняется только вместе с версией Jira.
    endpoint: str = SESSION_LOGIN_ENDPOINT

    def __init__(
        self,
        base_url: str,
        *,
        cookie_name: str = DEFAULT_SESSION_COOKIE,
        timeout: float = DEFAULT_TIMEOUT,
        verify_tls: bool = True,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        if not base_url:
            raise ValueError("Нужен base_url сервера Jira")
        self.base_url = base_url.rstrip("/")
        self.cookie_name = cookie_name or DEFAULT_SESSION_COOKIE
        self.timeout = float(timeout)
        self.verify_tls = bool(verify_tls)
        self._http_client = http_client
        #: Последний вызов: путь, код, длительность. Без тела и без заголовков.
        self.last_call: Dict[str, Any] = {}

    # -- служебное ----------------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        """Безопасное описание: никаких логинов, паролей и токенов."""
        return {
            "mode": "session_login",
            "endpoint": self.endpoint,
            "base_url": self.base_url,
            "cookie": self.cookie_name,
            "timeout": self.timeout,
            "verify_tls": self.verify_tls,
        }

    def __repr__(self) -> str:
        return "<SessionLoginProvider base_url=%s endpoint=%s>" % (self.base_url, self.endpoint)

    def _url(self) -> str:
        return self.base_url + self.endpoint

    @staticmethod
    def request_body(username: str, password: str) -> Dict[str, str]:
        """Тело запроса. Плоская форма — подтверждена пробой KON-30."""
        if not username or not password:
            raise ValueError("Нужны и логин, и пароль")
        return {"username": username, "password": password}

    # -- вход ---------------------------------------------------------------

    def login(self, username: str, password: str) -> SessionToken:
        """Войти в Jira и вернуть сессионную cookie.

        Исключения по причинам отказа:

        * :class:`JiraLoginError` — ``login_reason`` ``auth_failed`` или
          ``login_blocked`` (Jira просит подтвердить вход в браузере);
        * :class:`JiraUnavailableError` — сеть/TLS: Jira недоступна;
        * :class:`JiraTimeoutError` — превышен лимит 10 с (NFR-009).

        Тело запроса (в нём пароль) ни в одно из исключений не попадает.
        """
        body = self.request_body(username, password)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(
            timeout=self.timeout,
            verify=self.verify_tls,
            follow_redirects=False,
        )
        started = time.perf_counter()
        try:
            response = client.post(self._url(), json=body, headers=headers, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            self.last_call = {
                "method": "POST",
                "path": self.endpoint,
                "status": None,
                "ms": round(elapsed, 1),
                "outcome": "timeout",
            }
            raise JiraTimeoutError(
                "Превышен лимит %.0f с на вызов Jira (NFR-009)" % self.timeout,
                timeout=self.timeout,
                method="POST",
                path=self.endpoint,
            ) from exc
        except httpx.HTTPError as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            self.last_call = {
                "method": "POST",
                "path": self.endpoint,
                "status": None,
                "ms": round(elapsed, 1),
                "outcome": "network",
            }
            # Тип и текст исключения httpx не содержат тело запроса, поэтому
            # пароль в сообщение не попадает.
            raise JiraUnavailableError(
                "%s: %s" % (type(exc).__name__, exc), method="POST", path=self.endpoint
            ) from exc
        finally:
            # Пароль живёт только в теле одного запроса.
            body.clear()
            if owns_client:
                client.close()

        elapsed = (time.perf_counter() - started) * 1000.0
        self.last_call = {
            "method": "POST",
            "path": self.endpoint,
            "status": response.status_code,
            "ms": round(elapsed, 1),
            "bytes": len(response.content),
            "outcome": "ok" if response.status_code < 400 else "error",
        }

        if response.status_code >= 400:
            if response.status_code in (401, 403):
                raise self._login_error(response, password)
            raise error_for_response(response, method="POST", path=self.endpoint)

        cookie_name, token = self._extract_session(response)
        return SessionToken(
            cookie_name=cookie_name,
            token=token,
            created_at=datetime.now(timezone.utc),
            username=username,
        )

    # -- разбор ответа ------------------------------------------------------

    def _login_error(self, response: httpx.Response, password: str = "") -> JiraLoginError:
        """Собрать исключение отказа входа. Тело **запроса** здесь не участвует.

        Пароль известен только как аргумент: если Jira отзеркалила его в
        ответе, он заменяется на ``[скрыто]`` и в тексте для пользователя
        (FR-029), и в ``summary`` / ``body_head``.
        """
        content_type = response.headers.get("content-type", "")
        parsed = parse_error_body(response.content, content_type)
        secrets = (password,)
        return JiraLoginError(
            mask_secrets(parsed["summary"], secrets),
            user_text=extract_user_text(response.content, content_type, secrets),
            status=response.status_code,
            code=parsed["code"],
            login_reason=classify_login_failure(response),
            field_errors=parsed["field_errors"],
            body_head=mask_secrets(parsed["raw_head"], secrets),
            is_json=parsed["is_json"],
            method="POST",
            path=self.endpoint,
        )

    def _extract_session(self, response: httpx.Response) -> Tuple[str, str]:
        """Достать имя cookie и её значение из успешного ответа.

        Jira отдаёт и JSON ``{"session": {"name", "value"}}``, и
        ``Set-Cookie``; проба KON-30 читала заголовок. Поддерживаются обе
        формы, в порядке приоритета: документированная, затем заголовок.
        """
        payload = self._json_payload(response)
        if isinstance(payload, Mapping):
            session = payload.get("session")
            if isinstance(session, Mapping):
                value = session.get("value")
                if value:
                    return str(session.get("name") or self.cookie_name), str(value)

        for header in response.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            name = name.strip()
            value = rest.split(";", 1)[0].strip()
            if value and (name == self.cookie_name or not name):
                return name or self.cookie_name, value

        raise JiraError(
            "Jira ответила %s, но не прислала сессионную cookie" % response.status_code,
            status=response.status_code,
            method="POST",
            path=self.endpoint,
        )

    @staticmethod
    def _json_payload(response: httpx.Response) -> Any:
        content_type = (response.headers.get("content-type") or "").lower()
        if "json" not in content_type:
            return None
        try:
            return response.json()
        except ValueError:
            return None


def build_auth(
    *,
    auth_mode: str,
    session_token: Optional[str] = None,
    session_cookie: str = "JSESSIONID",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> AuthStrategy:
    """Собрать стратегию по режиму из конфига."""
    if auth_mode == "cookie":
        return SessionTokenAuth(session_token or "", session_cookie)
    if auth_mode == "basic":
        return BasicAuth(username or "", password or "")
    return AnonymousAuth()
