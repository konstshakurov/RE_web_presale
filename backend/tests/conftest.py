# -*- coding: utf-8 -*-
"""Общие фикстуры тестов backend.

``jira_client`` живёт в ``src/`` репозитория и не установлен как пакет, поэтому
каталог добавляется в ``sys.path`` — тот же приём, что в
``src/jira_client/tests/conftest.py`` и в ``tests/conftest.py`` корня.

Тесты не ходят в сеть: используется ``httpx.MockTransport``. Ключ шифрования
генерируется на каждый запуск и в репозиторий не попадает.
"""

from __future__ import annotations

import base64
import os
import sys
from typing import Callable, List

import httpx
import pytest

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_ROOT = os.path.join(os.path.dirname(_BACKEND_ROOT), "src")
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from jira_client import JiraConfig, TokenVault  # noqa: E402

from app.integrations.jira import JiraAdapter  # noqa: E402

#: Заведомо фиктивный адрес. Секретом не является.
FAKE_BASE_URL = "https://jira.example.internal"
#: Заведомо фиктивный токен сессии для ответов мока.
FAKE_RAW_TOKEN = "TESTSESSIONTOKEN0000"

JSON_HEADERS = {"content-type": "application/json"}

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture()
def vault() -> TokenVault:
    """Свежий ключ шифрования на каждый тест: в фикстурах секретов нет."""
    return TokenVault.from_base64(base64.b64encode(os.urandom(32)).decode("ascii"))


@pytest.fixture()
def make_config() -> Callable[..., JiraConfig]:
    def factory(**overrides) -> JiraConfig:
        base = dict(
            base_url=FAKE_BASE_URL,
            auth_mode="cookie",
            timeout=10.0,
            verify_tls=True,
        )
        base.update(overrides)
        return JiraConfig(**base)

    return factory


@pytest.fixture()
def adapter_factory(vault, make_config):
    """Создать адаптер поверх MockTransport; сеть не открывается."""
    adapters: List[JiraAdapter] = []
    clients: List[httpx.Client] = []

    def factory(handler: Handler, **overrides) -> JiraAdapter:
        http = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(http)
        kwargs = dict(config=make_config(), vault=vault, http_client=http)
        kwargs.update(overrides)
        adapter = JiraAdapter(**kwargs)
        adapters.append(adapter)
        return adapter

    yield factory

    for adapter in adapters:
        adapter.close()
    for http in clients:
        http.close()


def response(payload, status: int = 200, headers=None) -> httpx.Response:
    """JSON-ответ Jira."""
    merged = dict(JSON_HEADERS)
    merged.update(headers or {})
    if payload is None:
        return httpx.Response(status, content=b"", headers=merged)
    return httpx.Response(status, json=payload, headers=merged)


def error_response(messages, status: int = 401, headers=None) -> httpx.Response:
    return response({"errorMessages": list(messages), "errors": {}}, status, headers)


def session_response(
    value: str = FAKE_RAW_TOKEN, cookie_name: str = "JSESSIONID"
) -> httpx.Response:
    return response({"session": {"name": cookie_name, "value": value}})
