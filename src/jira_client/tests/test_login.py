# -*- coding: utf-8 -*-
"""Вход по паролю: успешный обмен, классификация отказов, отсутствие утечки.

Проверяется по критериям приёмки Dev-18:

* верные данные → токен; неверный пароль → ``auth_failed``; блокировка →
  ``login_blocked``; недоступная Jira → ``jira_unavailable`` / ``jira_timeout``,
  а **не** «неверный пароль»;
* пароль не попадает в ``repr``, в ``last_call``, в исключения и в состояние
  провайдера;
* повторов нет, лимит 10 с на вызов (NFR-009) задаётся явно.

Сеть не используется: ``httpx.MockTransport``.
"""

from __future__ import annotations

import httpx
import pytest

from jira_client.auth import SessionLoginProvider
from jira_client.errors import (
    LOGIN_REASON_AUTH_FAILED,
    LOGIN_HINTS,
    LOGIN_REASON_BLOCKED,
    USER_TEXT_LIMIT,
    JiraBadRequestError,
    JiraLoginError,
    JiraServerError,
    JiraTimeoutError,
    JiraUnavailableError,
)

BASE = "https://jira.example.internal"
ENDPOINT = "/rest/auth/1/session"

#: Обезличенные данные. Секретом не являются и в настоящих системах не значатся.
USERNAME = "probe-user"
PASSWORD = "FAKE_temporary-login-password"

#: Токен в ответе фиктивный, тоже не является секретом.
FAKE_SESSION = "FAKESESSIONTOKENVALUE"

JSON_CT = {"content-type": "application/json"}


def build(handler, **kwargs):
    """Провайдер с транспортом MockTransport (сеть не открывается)."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SessionLoginProvider(BASE, http_client=client, **kwargs)


def json_ok(session_name="JSESSIONID", value=FAKE_SESSION):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"session": {"name": session_name, "value": value}}, headers=JSON_CT)

    return handler


def json_error(status, messages, headers=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"errorMessages": list(messages), "errors": {}},
            headers={**JSON_CT, **(headers or {})},
        )

    return handler


class TestLoginSuccess:
    def test_returns_session_token(self):
        provider = build(json_ok())
        session = provider.login(USERNAME, PASSWORD)
        assert session.token == FAKE_SESSION
        assert session.cookie_name == "JSESSIONID"
        assert session.username == USERNAME
        assert session.created_at.tzinfo is not None

    def test_body_is_flat_and_confirmed_by_probe(self):
        """Плоское тело ``{"username", "password"}``; вложенная форма даёт 400."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content.decode("utf-8")
            seen["content_type"] = request.headers.get("content-type", "")
            return json_ok()(request)

        build(handler).login(USERNAME, PASSWORD)
        import json as _json

        parsed = _json.loads(seen["body"])
        assert set(parsed) == {"username", "password"}
        assert parsed["username"] == USERNAME
        assert parsed["password"] == PASSWORD
        assert seen["content_type"].startswith("application/json")

    def test_endpoint_and_method(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["method"] = request.method
            return json_ok()(request)

        build(handler).login(USERNAME, PASSWORD)
        assert (seen["method"], seen["path"]) == ("POST", ENDPOINT)

    def test_set_cookie_fallback_when_body_has_no_session(self):
        """Проба KON-30 читала ``Set-Cookie``; обе формы поддерживаются."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"", headers={"set-cookie": "JSESSIONID=fromheader; Path=/"}
            )

        session = build(handler).login(USERNAME, PASSWORD)
        assert session.token == "fromheader"
        assert session.cookie_name == "JSESSIONID"

    def test_custom_cookie_name_is_honoured(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"session": {"name": "atl_token", "value": "v"}})

        session = build(handler, cookie_name="atl_token").login(USERNAME, PASSWORD)
        assert session.cookie_name == "atl_token"

    def test_missing_credentials_fails_before_any_request(self):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("запрос не должен выполняться")

        with pytest.raises(ValueError):
            build(handler).login("", "")


class TestLoginFailures:
    """Отказы входа различаются признаками, а не сводятся к «неверный пароль»."""

    def test_wrong_password_is_auth_failed(self):
        provider = build(
            json_error(
                401,
                ["Ошибка доступа"],
                {"x-seraph-loginreason": "AUTHENTICATED_FAILED"},
            )
        )
        with pytest.raises(JiraLoginError) as exc:
            provider.login(USERNAME, PASSWORD)
        assert exc.value.login_reason == LOGIN_REASON_AUTH_FAILED
        assert exc.value.status == 401

    def test_captcha_or_lockout_is_login_blocked(self):
        provider = build(
            json_error(
                401,
                ["Проверка не пройдена"],
                {"x-seraph-loginreason": "AUTHENTICATION_DENIED"},
            )
        )
        with pytest.raises(JiraLoginError) as exc:
            provider.login(USERNAME, PASSWORD)
        assert exc.value.login_reason == LOGIN_REASON_BLOCKED

    def test_permission_text_is_blocked_not_wrong_password(self):
        """«Вы не авторизованы» — отказ в доступе к входу, а не неверный пароль."""
        provider = build(json_error(401, ["Вы не авторизованы. Для выполнения этой операции требуется аутентификация."]))
        with pytest.raises(JiraLoginError) as exc:
            provider.login(USERNAME, PASSWORD)
        assert exc.value.login_reason == LOGIN_REASON_BLOCKED

    def test_html_answer_is_compressed_not_shown(self):
        html = (
            "<html><head><title>Перенаправление на страницу входа Jira</title></head>"
            + "<body>" + ("x" * 13000) + "</body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text=html, headers={"content-type": "text/html"})

        with pytest.raises(JiraLoginError) as exc:
            build(handler).login(USERNAME, PASSWORD)
        assert "<html" not in str(exc.value)
        assert len(str(exc.value)) < 600
        assert "Перенаправление" in exc.value.summary

    def test_network_error_is_jira_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(JiraUnavailableError) as exc:
            build(handler).login(USERNAME, PASSWORD)
        assert not isinstance(exc.value, JiraLoginError)
        assert "connection refused" in str(exc.value)

    def test_timeout_is_jira_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out")

        with pytest.raises(JiraTimeoutError) as exc:
            build(handler).login(USERNAME, PASSWORD)
        assert exc.value.timeout == 10.0
        assert "NFR-009" in str(exc.value)

    def test_server_error_is_not_wrong_password(self):
        with pytest.raises(JiraServerError) as exc:
            build(json_error(500, ["Внутренняя ошибка"])).login(USERNAME, PASSWORD)
        assert not isinstance(exc.value, JiraLoginError)

    def test_bad_request_is_not_wrong_password(self):
        with pytest.raises(JiraBadRequestError):
            build(json_error(400, ['Unrecognized field "session"'])).login(USERNAME, PASSWORD)

    def test_hint_is_human_readable_and_free_of_html(self):
        provider = build(
            json_error(401, [], {"x-seraph-loginreason": "AUTHENTICATION_DENIED"})
        )
        with pytest.raises(JiraLoginError) as exc:
            provider.login(USERNAME, PASSWORD)
        assert "браузер" in exc.value.hint
        assert "<" not in exc.value.hint


class TestPasswordDoesNotLeak:
    """Пароль живёт только в теле одного запроса (Dev-18, NFR-006)."""

    @staticmethod
    def _state(provider):
        """Состояние провайдера без HTTP-клиента (в нём тела запросов нет)."""
        return {k: v for k, v in vars(provider).items() if k != "_http_client"}

    def test_password_is_sent_once(self):
        """Доказательство, что проверка ниже вообще проходит через login."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content.decode("utf-8")
            return json_ok()(request)

        build(handler).login(USERNAME, PASSWORD)
        import json as _json

        sent = _json.loads(seen["body"])
        assert sent["password"] == PASSWORD
        assert sent["username"] == USERNAME

    def test_password_not_in_repr_or_describe(self):
        provider = build(json_ok())
        provider.login(USERNAME, PASSWORD)
        assert PASSWORD not in repr(provider)
        assert PASSWORD not in str(provider.describe())
        assert PASSWORD not in repr(provider.last_call)
        assert PASSWORD not in repr(self._state(provider))

    def test_password_not_in_state_after_failed_login(self):
        provider = build(
            json_error(401, ["Ошибка доступа"], {"x-seraph-loginreason": "AUTHENTICATED_FAILED"})
        )
        with pytest.raises(JiraLoginError):
            provider.login(USERNAME, PASSWORD)
        assert PASSWORD not in repr(self._state(provider))

    def test_password_not_in_exception_text_or_fields(self):
        provider = build(
            json_error(401, ["Ошибка доступа"], {"x-seraph-loginreason": "AUTHENTICATED_FAILED"})
        )
        with pytest.raises(JiraLoginError) as exc:
            provider.login(USERNAME, PASSWORD)
        err = exc.value
        assert PASSWORD not in str(err)
        assert PASSWORD not in repr(err.__dict__)
        assert PASSWORD not in err.summary
        assert PASSWORD not in err.body_head

    def test_password_not_in_network_exception(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(JiraUnavailableError) as exc:
            build(handler).login(USERNAME, PASSWORD)
        assert PASSWORD not in str(exc.value)

    def test_last_call_records_no_body_and_no_headers(self):
        provider = build(json_ok())
        provider.login(USERNAME, PASSWORD)
        assert set(provider.last_call) <= {"method", "path", "status", "ms", "bytes", "outcome"}
        assert provider.last_call["path"] == ENDPOINT
        assert provider.last_call["status"] == 200

    def test_username_and_token_not_in_describe(self):
        provider = build(json_ok())
        session = provider.login(USERNAME, PASSWORD)
        described = str(provider.describe())
        assert PASSWORD not in described
        assert USERNAME not in described
        assert session.token not in described


class TestUserTextFromJira:
    """FR-029 (KON-69): отказ показывается текстом из JSON-ответа Jira."""

    DENIED = {"x-seraph-loginreason": "AUTHENTICATION_DENIED"}

    def _fail(self, handler):
        with pytest.raises(JiraLoginError) as exc:
            build(handler).login(USERNAME, PASSWORD)
        return exc.value

    def test_json_error_messages_are_shown_as_is(self):
        text = "Login denied: confirmation in the browser is required (fake Jira, QA)"
        err = self._fail(json_error(403, [text], self.DENIED))
        assert err.login_reason == LOGIN_REASON_BLOCKED
        assert err.hint == text

    def test_wrong_password_text_is_shown_too(self):
        err = self._fail(
            json_error(401, ["Ошибка доступа"], {"x-seraph-loginreason": "AUTHENTICATED_FAILED"})
        )
        assert err.login_reason == LOGIN_REASON_AUTH_FAILED
        assert err.hint == "Ошибка доступа"

    def test_errors_dict_and_several_messages_are_joined(self):
        def handler(request):
            return httpx.Response(
                403,
                json={"errorMessages": ["Первая", "Вторая"], "errors": {"login": "заблокирован"}},
                headers={**JSON_CT, **self.DENIED},
            )

        assert self._fail(handler).hint == "Первая Вторая login: заблокирован"

    def test_html_gives_generic_hint(self):
        def handler(request):
            return httpx.Response(
                403,
                text="<html><title>Denied</title><body>page</body></html>",
                headers={"content-type": "text/html", **self.DENIED},
            )

        err = self._fail(handler)
        assert err.user_text == ""
        assert err.hint == LOGIN_HINTS[LOGIN_REASON_BLOCKED]

    def test_empty_body_gives_generic_hint(self):
        def handler(request):
            return httpx.Response(403, content=b"", headers=self.DENIED)

        assert self._fail(handler).hint == LOGIN_HINTS[LOGIN_REASON_BLOCKED]

    def test_json_without_messages_gives_generic_hint(self):
        err = self._fail(json_error(403, [], self.DENIED))
        assert err.hint == LOGIN_HINTS[LOGIN_REASON_BLOCKED]

    def test_password_echo_is_masked_everywhere(self):
        err = self._fail(json_error(403, ["Пароль %s не принят" % PASSWORD], self.DENIED))
        assert PASSWORD not in err.hint
        assert "[скрыто]" in err.hint
        assert PASSWORD not in str(err) and PASSWORD not in err.summary
        assert PASSWORD not in err.body_head

    def test_long_text_is_truncated(self):
        err = self._fail(json_error(403, ["я" * 2000], self.DENIED))
        assert len(err.hint) == USER_TEXT_LIMIT

    def test_control_characters_are_removed(self):
        raw = "строка1\u0000\r\nстрока2\u001b[31m"
        err = self._fail(json_error(403, [raw], self.DENIED))
        assert "\x00" not in err.hint and "\r" not in err.hint and "\n" not in err.hint
        assert err.hint.startswith("строка1 строка2")
