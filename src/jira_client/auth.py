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
    ``POST /rest/auth/1/session`` с телом ``{"username": ..., "password": ...}``.
    Форма запроса подтверждена пробой, но сам метод требует пароль пользователя
    и потому оставлен **отключённым**: закладывать хранение паролей в код
    запрещено UC-005 и NFR-006. Класс существует как документированная точка
    расширения и не вызывается клиентом по умолчанию.

Секреты никуда не логируются: у всех стратегий есть безопасное описание.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


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


#: Форма тела, подтверждённая пробой (см. probe_raw: плоское тело дало
#: 401 c x-seraph-loginreason: AUTHENTICATED_FAILED, вложенное {"session": ...} —
#: 400 "Unrecognized field \"session\" (Class com.atlassian.jira.rest.auth.AuthParams)").
SESSION_LOGIN_BODY_FIELDS = ("username", "password")


@dataclass
class SessionLoginProvider:
    """Обмен логина и пароля на сессионный токен через ``/rest/auth/1/session``.

    НЕ вызывается клиентом по умолчанию. Оставлен потому, что без него
    невосстановима истёкшая сессия, а это операция, требующая пароля, и её
    применение должно быть отдельно согласовано с владельцем (см. отчёт
    KON-30, раздел «Риски»).
    """

    base_url: str
    enabled: bool = False
    endpoint: str = "/rest/auth/1/session"

    def __post_init__(self) -> None:
        if not self.enabled:
            raise PermissionError(
                "Обмен пароля на сессию отключён: он требует хранения пароля "
                "пользователя (UC-005, NFR-006). Включать только по решению владельца."
            )

    def request_body(self, username: str, password: str) -> Mapping[str, str]:
        if not username or not password:
            raise ValueError("Нужны и логин, и пароль")
        return {"username": username, "password": password}


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