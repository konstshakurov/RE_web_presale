# -*- coding: utf-8 -*-
"""Общие фикстуры тестов клиента Jira.

Тесты не ходят в сеть: используется ``httpx.MockTransport``. Все примеры
ответов обезличены — реальных ключей задач, имён, адресов и токенов в
фикстурах нет (правило пробы KON-30).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import httpx
import pytest

#: Позволяет запускать pytest из src/jira_client без установки пакета.
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from jira_client.config import JiraConfig  # noqa: E402

#: Заведомо фиктивные значения. Секретом не являются.
FAKE_TOKEN = "TESTSESSIONTOKEN0000"
FAKE_BASE_URL = "https://jira.example.internal"

#: Обезличенные ответы, повторяющие форму настоящих ответов Jira.
AUTH_OK_BODY = {
    "self": "https://jira.example.internal/rest/api/2/user?accountId=fake-id",
    "accountId": "fake-id",
    "displayName": "Тестовый Пользователь",
    "accountType": "atlassian",
    "active": True,
}

NO_AUTH_BODY = {"message": "Client must be authenticated to access this resource.", "status-code": 401}
#: Отказ аутентификации на POST /rest/auth/1/session при неверных учётных
#: данных. Проба зафиксировала заголовок x-seraph-loginreason:
#: AUTHENTICATED_FAILED и именно это тело.
AUTH_FAILED_BODY = {"errorMessages": ["Ошибка доступа"], "errors": {}}
#: Пользователь аутентифицирован, но прав не хватает. Jira 8.13.0 отдаёт
#: этот случай кодом 401, а не 403 — поэтому различать приходится по телу.
NO_PERMISSION_BODY = {"errorMessages": ["Вы не авторизованы. Для выполнения этой операции требуется аутентификация."], "errors": {}}
NOT_FOUND_BODY = {"errorMessages": ["ЗАПРОС НЕ СУЩЕСТВУЕТ"], "errors": {}}
#: Отказ доступа к объекту без аутентификации. Jira отдаёт его кодом 401.
NO_ACCESS_BODY = {"errorMessages": ["У вас нет доступа для просмотра данного запроса.", "Необходимо ввести имя пользователя"], "errors": {}}


def make_config(**overrides: Any) -> JiraConfig:
    """Конфиг с фиктивным токеном."""
    values = {
        "base_url": FAKE_BASE_URL,
        "auth_mode": "cookie",
        "session_token": FAKE_TOKEN,
        "session_cookie": "JSESSIONID",
        "username": None,
        "password": None,
        "token_encryption_key": None,
        "timeout": 10.0,
        "verify_tls": True,
    }
    values.update(overrides)
    return JiraConfig(**values)


class RecordedRequest:
    """Один запрошенный вызов — для проверок в тестах."""

    def __init__(self, request: httpx.Request) -> None:
        self.method = request.method
        self.url = str(request.url)
        self.path = request.url.path
        self.params = dict(request.url.params)
        self.headers = dict(request.headers)
        self.raw_content = request.content
        try:
            self.json = json.loads(request.content.decode("utf-8")) if request.content else None
        except (ValueError, UnicodeDecodeError):
            self.json = None

    def header(self, name: str) -> Optional[str]:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def __repr__(self) -> str:  # pragma: no cover - только для отладки
        return "<%s %s>" % (self.method, self.path)


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    config: Optional[JiraConfig] = None,
    **client_kwargs: Any,
) -> Tuple[Any, List[RecordedRequest]]:
    """Клиент на поддельном транспорте + список выполненных вызовов."""
    from jira_client.client import JiraClient

    recorded: List[RecordedRequest] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        recorded.append(RecordedRequest(request))
        return handler(request)

    transport = httpx.MockTransport(wrapped)
    http_client = httpx.Client(transport=transport, timeout=10.0, follow_redirects=False)
    client = JiraClient(config or make_config(), http_client=http_client, **client_kwargs)
    return client, recorded


def json_response(payload: Any, status: int = 200, headers: Optional[Mapping[str, str]] = None) -> httpx.Response:
    """Ответ с JSON-телом."""
    base = {"content-type": "application/json;charset=UTF-8"}
    base.update(headers or {})
    return httpx.Response(status_code=status, content=json.dumps(payload).encode("utf-8"), headers=base)


def error_response(payload: Any, status: int) -> httpx.Response:
    """Ответ с ошибкой в теле Jira."""
    return json_response(payload, status=status)


def html_response(text: str, status: int = 401) -> httpx.Response:
    """HTML-страница отказа — так Jira отвечает на неверный Basic auth."""
    return httpx.Response(
        status_code=status,
        content=("<html><body>%s</body></html>" % text).encode("utf-8"),
        headers={"content-type": "text/html;charset=UTF-8"},
    )


@pytest.fixture
def config() -> JiraConfig:
    return make_config()


@pytest.fixture
def client_factory():
    """Фикстура-фабрика: ``client, calls = client_factory(handler)``."""
    created = []

    def factory(handler: Callable[[httpx.Request], httpx.Response], **kw: Any):
        client, calls = make_client(handler, **kw)
        created.append(client)
        return client, calls

    yield factory
    for client in created:
        client.close()


@pytest.fixture
def sample_issue() -> Dict[str, Any]:
    """Обезличенная задача с правилами и ставкой."""
    return {
        "id": "10001",
        "key": "ANALYTICS-0001",
        "fields": {
            "summary": "Тестовая потребность",
            "description": "Проверка клиента.",
            "status": {"id": "3", "name": "Тестовый статус", "statusCategory": {"key": "new"}},
            "issuetype": {"id": "10010", "name": "Demand"},
            "created": "2026-09-01T10:00:00.000+0300",
            "updated": "2026-10-04T12:00:00.000+0300",
            "assignee": {"name": "testuser", "displayName": "Тестовый Пользователь"},
            "customfield_10101": "SA-0001",
            "customfield_20809": "1000",
            "customfield_20810": "2000",
            "customfield_20302": None,
            "customfield_20914": "Запрещено",
        },
    }