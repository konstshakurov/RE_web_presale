# -*- coding: utf-8 -*-
"""Адаптер Jira: вход, проверка сессии, чтение, карта ошибок (Dev-18).

Все проверки идут через ``httpx.MockTransport`` — сеть не открывается.
Фикстуры обезличены: реальных ключей задач, логинов, паролей и токенов здесь нет.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.integrations.jira import (
    CODE_AUTH_FAILED,
    CODE_JIRA_UNAVAILABLE,
    CODE_LOGIN_BLOCKED,
    CODE_NEEDS_REAUTHORIZATION,
    CODE_NO_PERMISSION,
    JiraIntegrationError,
    JiraPort,
    OPERATIONAL_CODES,
    SESSION_JIRA_UNAVAILABLE,
    SESSION_NEEDS_REAUTHORIZATION,
    SESSION_NO_PERMISSION,
    SESSION_OK,
)

from .conftest import FAKE_RAW_TOKEN, error_response, response, session_response

ENDPOINT = "/rest/auth/1/session"
MYSELF_PATH = "/rest/api/2/myself"
SEARCH_PATH = "/rest/api/2/search"

#: Обезличенные данные: значатся только в этом тесте.
PROBE_USER = "probe-user"
PROBE_PASSWORD = "temporary-login-password"

#: Синтетические ключи: номера меньше сотни (правило фикстур KON-30).
DEMAND_KEY = "ANALYTICS-1"
EPIC_KEY = "SA-1"

#: Ответ ``/rest/api/2/myself`` по умолчанию.
MYSELF_OK = lambda: response({"name": PROBE_USER, "displayName": "Probe"})  # noqa: E731


def router(*, default=None, **routes):
    """Раздать ответы по точному пути; ``default`` — для остальных путей.

    Фабрика нужна, потому что один объект ``httpx.Response`` вторично
    отправлен быть не может.
    """
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        factory = routes.get(request.url.path, default)
        if factory is None:
            raise AssertionError("неожиданный путь запроса: %s" % request.url.path)
        return factory() if callable(factory) else factory

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


def login_routes():
    """Маршруты входа: ``POST /rest/auth/1/session`` отдаёт сессию."""
    return {ENDPOINT: lambda: session_response()}


def request_to(handler, path):
    for request in handler.calls:
        if request.url.path == path:
            return request
    raise AssertionError("запроса к %s не было: %s" % (path, [r.url.path for r in handler.calls]))


def _search_page(keys, total=None, start_at=0):
    return {
        "startAt": start_at,
        "maxResults": len(keys),
        "total": len(keys) if total is None else total,
        "issues": [{"key": key, "fields": {"summary": key}} for key in keys],
    }


# ---------------------------------------------------------------------------
# 1. Вход
# ---------------------------------------------------------------------------


class TestLogin:
    def test_returns_sealed_session_with_creation_time(self, vault, adapter_factory):
        handler = router(**login_routes())
        adapter = adapter_factory(handler)

        session = adapter.login(PROBE_USER, PROBE_PASSWORD)

        assert session.cookie_name == "JSESSIONID"
        assert session.created_at.tzinfo is not None
        assert (datetime.now(timezone.utc) - session.created_at).total_seconds() < 60
        # Токен наружу уходит только зашифрованным (NFR-006).
        assert session.sealed_token != FAKE_RAW_TOKEN
        assert vault.open(session.sealed_token) == FAKE_RAW_TOKEN

    def test_implements_port(self, adapter_factory):
        handler = router(**login_routes())
        adapter = adapter_factory(handler)
        assert isinstance(adapter, JiraPort)

    def test_body_is_flat_and_password_is_only_on_the_wire(self, adapter_factory):
        handler = router(**login_routes())
        adapter = adapter_factory(handler)

        session = adapter.login(PROBE_USER, PROBE_PASSWORD)

        body = json.loads(request_to(handler, ENDPOINT).content.decode("utf-8"))
        assert set(body) == {"username", "password"}
        assert body["password"] == PROBE_PASSWORD

        for text in (repr(session), str(session.describe()), str(adapter.describe())):
            assert PROBE_PASSWORD not in text
            assert FAKE_RAW_TOKEN not in text

    def test_wrong_password_is_auth_failed(self, adapter_factory):
        handler = router(
            default=lambda: error_response(
                ["Ошибка доступа"], 401, {"x-seraph-loginreason": "AUTHENTICATED_FAILED"}
            )
        )
        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, PROBE_PASSWORD)

        assert exc.value.code == CODE_AUTH_FAILED
        assert PROBE_PASSWORD not in str(exc.value)

    def test_captcha_is_login_blocked(self, adapter_factory):
        handler = router(
            default=lambda: error_response(
                ["Проверка не пройдена"], 401, {"x-seraph-loginreason": "AUTHENTICATION_DENIED"}
            )
        )
        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, PROBE_PASSWORD)

        assert exc.value.code == CODE_LOGIN_BLOCKED
        assert exc.value.code != CODE_AUTH_FAILED
        assert "браузер" in exc.value.message

    def test_unreachable_jira_is_jira_unavailable_not_wrong_password(self, adapter_factory):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, PROBE_PASSWORD)

        assert exc.value.code == CODE_JIRA_UNAVAILABLE
        assert exc.value.code != CODE_AUTH_FAILED
        assert exc.value.retryable is True

    def test_timeout_is_jira_unavailable_not_wrong_password(self, adapter_factory):
        def handler(request):
            raise httpx.ReadTimeout("read timed out")

        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, PROBE_PASSWORD)

        assert exc.value.code == CODE_JIRA_UNAVAILABLE

    def test_server_error_is_jira_unavailable(self, adapter_factory):
        handler = router(default=lambda: error_response(["ошибка"], 500))
        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, PROBE_PASSWORD)

        assert exc.value.code == CODE_JIRA_UNAVAILABLE


# ---------------------------------------------------------------------------
# 2. check_session
# ---------------------------------------------------------------------------


class TestCheckSession:
    @staticmethod
    def _signed_in(adapter_factory, read):
        handler = router(default=read, **login_routes())
        adapter = adapter_factory(handler)
        session = adapter.login(PROBE_USER, PROBE_PASSWORD)
        return adapter, session, handler

    def test_ok_after_login(self, adapter_factory):
        adapter, session, _ = self._signed_in(adapter_factory, MYSELF_OK)

        check = adapter.check_session(session.sealed_token)

        assert check.state == SESSION_OK
        assert check.ok is True
        assert check.needs_reauthorization is False

    def test_expired_session_needs_reauthorization(self, adapter_factory):
        adapter, session, _ = self._signed_in(
            adapter_factory, lambda: error_response(["Необходимо ввести имя пользователя"], 401)
        )

        check = adapter.check_session(session.sealed_token)

        assert check.state == SESSION_NEEDS_REAUTHORIZATION
        assert check.reason == "login_required"

    def test_html_answer_is_needs_reauthorization_and_not_html(self, adapter_factory):
        html = "<html><head><title>Вход в Jira</title></head><body>%s</body></html>" % ("x" * 13000)
        adapter, session, _ = self._signed_in(
            adapter_factory,
            lambda: httpx.Response(401, text=html, headers={"content-type": "text/html"}),
        )

        check = adapter.check_session(session.sealed_token)

        assert check.state == SESSION_NEEDS_REAUTHORIZATION
        assert "<html" not in check.summary
        assert len(check.summary) < 400

    def test_unavailable_jira_is_not_reauthorization(self, adapter_factory):
        def handler(request):
            if request.url.path == ENDPOINT:
                return session_response()
            raise httpx.ConnectError("connection refused")

        adapter = adapter_factory(handler)
        session = adapter.login(PROBE_USER, PROBE_PASSWORD)

        check = adapter.check_session(session.sealed_token)

        assert check.state == SESSION_JIRA_UNAVAILABLE
        assert check.needs_reauthorization is False

    def test_timeout_is_not_reauthorization(self, adapter_factory):
        def handler(request):
            if request.url.path == ENDPOINT:
                return session_response()
            raise httpx.ReadTimeout("read timed out")

        adapter = adapter_factory(handler)
        session = adapter.login(PROBE_USER, PROBE_PASSWORD)

        assert adapter.check_session(session.sealed_token).state == SESSION_JIRA_UNAVAILABLE

    def test_server_error_is_not_reauthorization(self, adapter_factory):
        adapter, session, _ = self._signed_in(
            adapter_factory, lambda: error_response(["сбой"], 500)
        )

        assert adapter.check_session(session.sealed_token).state == SESSION_JIRA_UNAVAILABLE

    def test_no_permission_is_distinguished_from_expired_session(self, adapter_factory):
        adapter, session, _ = self._signed_in(
            adapter_factory,
            lambda: error_response(
                ["Вы не авторизованы. Для выполнения этой операции требуется аутентификация."], 401
            ),
        )

        check = adapter.check_session(session.sealed_token)

        assert check.state == SESSION_NO_PERMISSION
        assert check.state != SESSION_NEEDS_REAUTHORIZATION
        assert check.reason == CODE_NO_PERMISSION

    def test_missing_token_needs_reauthorization(self, adapter_factory):
        handler = router(**login_routes())
        adapter = adapter_factory(handler)

        check = adapter.check_session("")

        assert check.state == SESSION_NEEDS_REAUTHORIZATION
        assert check.reason == "no_credentials"

    def test_tampered_sealed_token_needs_reauthorization(self, adapter_factory):
        handler = router(**login_routes())
        adapter = adapter_factory(handler)

        tampered = base64.b64encode(b"x" * 32).decode("ascii")
        check = adapter.check_session(tampered)

        assert check.state == SESSION_NEEDS_REAUTHORIZATION
        assert "не читается" in check.summary

    def test_check_session_reads_myself_exactly_once(self, adapter_factory):
        adapter, session, handler = self._signed_in(adapter_factory, MYSELF_OK)
        before = len(handler.calls)

        adapter.check_session(session.sealed_token)

        assert len(handler.calls) - before == 1
        assert handler.calls[-1].url.path == MYSELF_PATH


# ---------------------------------------------------------------------------
# 3. Чтение
# ---------------------------------------------------------------------------


def _signed_in(adapter_factory, read, **overrides):
    """Адаптер, вошедший в Jira; ``read`` — ответ на любой чтительный запрос."""
    handler = router(default=read, **login_routes())
    adapter = adapter_factory(handler, **overrides)
    session = adapter.login(PROBE_USER, PROBE_PASSWORD)
    return adapter, session, handler


class TestSearchIncremental:
    def test_jql_is_built_on_updated_and_ordered_ascending(self, adapter_factory):
        adapter, session, handler = _signed_in(
            adapter_factory, lambda: response(_search_page([DEMAND_KEY]))
        )

        issues = adapter.search_incremental(session.sealed_token, "2026-10-05 09:00")

        jql = request_to(handler, SEARCH_PATH).url.params["jql"]
        assert "updated >" in jql
        assert "2026-10-05 09:00" in jql
        assert "ORDER BY updated ASC" in jql
        assert "project = ANALYTICS" in jql
        # Возвращаются только задачи, вернувшиеся Jira по этому запросу.
        assert [issue["key"] for issue in issues] == [DEMAND_KEY]

    def test_only_changed_issues_are_returned(self, adapter_factory):
        adapter, session, _ = _signed_in(
            adapter_factory, lambda: response(_search_page([DEMAND_KEY, "ANALYTICS-2"]))
        )

        issues = adapter.search_incremental(session.sealed_token, "2026-10-05 09:00")

        assert {issue["key"] for issue in issues} == {DEMAND_KEY, "ANALYTICS-2"}
        assert all("fields" in issue for issue in issues)

    def test_field_list_is_configurable(self, adapter_factory):
        adapter, session, handler = _signed_in(
            adapter_factory,
            lambda: response(_search_page([])),
            read_fields=("summary", "status"),
            epic_fields=("summary",),
        )

        adapter.search_incremental(session.sealed_token, "2026-10-05 09:00")

        assert request_to(handler, SEARCH_PATH).url.params["fields"] == "summary,status"

    def test_project_filter_is_configurable(self, adapter_factory):
        adapter, session, handler = _signed_in(
            adapter_factory, lambda: response(_search_page([]))
        )

        adapter.search_incremental(session.sealed_token, "2026-10-05 09:00", project="SA")

        assert "project = SA" in request_to(handler, SEARCH_PATH).url.params["jql"]


class TestGetIssue:
    ISSUE_PATH = "/rest/api/2/issue/%s" % DEMAND_KEY

    def test_reads_issue_with_configurable_fields(self, adapter_factory):
        payload = {"key": DEMAND_KEY, "fields": {"summary": "запрос", "status": {"name": "Открыта"}}}
        adapter, session, handler = _signed_in(
            adapter_factory, lambda: response(payload), read_fields=("summary", "status")
        )

        issue = adapter.get_issue(session.sealed_token, DEMAND_KEY)

        assert request_to(handler, self.ISSUE_PATH).url.params["fields"] == "summary,status"
        assert issue["key"] == DEMAND_KEY

    def test_explicit_fields_override_defaults(self, adapter_factory):
        adapter, session, handler = _signed_in(
            adapter_factory, lambda: response({"key": DEMAND_KEY}), read_fields=("summary",)
        )

        adapter.get_issue(session.sealed_token, DEMAND_KEY, fields=("description",))

        assert request_to(handler, self.ISSUE_PATH).url.params["fields"] == "description"


class TestEpic:
    EPIC_PATH = "/rest/agile/1.0/epic/%s" % EPIC_KEY
    EPIC_CHILDREN_PATH = "/rest/agile/1.0/epic/%s/issue" % EPIC_KEY

    def test_children_come_from_agile_api_not_jql_parent(self, adapter_factory):
        payload = {
            "total": 2,
            "issues": [
                {"key": DEMAND_KEY, "fields": {"summary": "a"}},
                {"key": "ANALYTICS-2", "fields": {"summary": "b"}},
            ],
        }
        adapter, session, handler = _signed_in(adapter_factory, lambda: response(payload))

        children = adapter.get_epic_children(session.sealed_token, EPIC_KEY)

        assert request_to(handler, self.EPIC_CHILDREN_PATH)
        paths = [request.url.path for request in handler.calls]
        assert SEARCH_PATH not in paths, "дети эпика читались через JQL вместо Agile API"
        assert [child["key"] for child in children] == [DEMAND_KEY, "ANALYTICS-2"]

    def test_children_field_list_is_configurable(self, adapter_factory):
        adapter, session, handler = _signed_in(
            adapter_factory,
            lambda: response({"total": 0, "issues": []}),
            read_fields=("summary", "status"),
        )

        adapter.get_epic_children(session.sealed_token, EPIC_KEY)

        assert request_to(handler, self.EPIC_CHILDREN_PATH).url.params["fields"] == "summary,status"

    def test_get_epic_uses_agile_endpoint(self, adapter_factory):
        adapter, session, handler = _signed_in(
            adapter_factory, lambda: response({"key": EPIC_KEY, "id": "1"})
        )

        epic = adapter.get_epic(session.sealed_token, EPIC_KEY)

        assert epic["key"] == EPIC_KEY
        assert request_to(handler, self.EPIC_PATH)

    def test_epic_link_field_is_canonical_customfield_10101(self, adapter_factory):
        handler = router(**login_routes())
        adapter = adapter_factory(handler)
        assert adapter.epic_link_field == "customfield_10101"


# ---------------------------------------------------------------------------
# 4. Лимиты и повторы (NFR-009)
# ---------------------------------------------------------------------------


class TestCallLimits:
    ISSUE_PATH = "/rest/api/2/issue/%s" % DEMAND_KEY

    def test_timeout_is_10_seconds(self, adapter_factory):
        adapter, _, _ = _signed_in(adapter_factory, lambda: response({"key": DEMAND_KEY}))
        assert adapter.config.timeout == 10.0
        assert adapter.describe()["config"]["timeout"] == 10.0

    def test_retries_are_off(self, adapter_factory):
        """5xx не приводит к повтору: повтор внутри 10 с нарушил бы NFR-009."""
        adapter, session, handler = _signed_in(
            adapter_factory, lambda: error_response(["сбой"], 500)
        )

        with pytest.raises(JiraIntegrationError):
            adapter.get_issue(session.sealed_token, DEMAND_KEY)

        issue_calls = [r for r in handler.calls if r.url.path == self.ISSUE_PATH]
        assert len(issue_calls) == 1

    def test_one_request_per_read_operation(self, adapter_factory):
        adapter, session, handler = _signed_in(
            adapter_factory, lambda: response({"key": DEMAND_KEY})
        )
        before = len(handler.calls)

        adapter.get_issue(session.sealed_token, DEMAND_KEY)

        assert len(handler.calls) - before == 1


# ---------------------------------------------------------------------------
# 5. Единая карта ошибок
# ---------------------------------------------------------------------------


class TestUnifiedErrorMap:
    """Все отказы выходят из порта с одним признаком ``code`` (Dev-18)."""

    def test_map_contains_the_five_operational_codes(self):
        assert set(OPERATIONAL_CODES) == {
            CODE_NEEDS_REAUTHORIZATION,
            CODE_NO_PERMISSION,
            CODE_AUTH_FAILED,
            CODE_LOGIN_BLOCKED,
            CODE_JIRA_UNAVAILABLE,
        }

    def test_expired_session_during_read_is_needs_reauthorization(self, adapter_factory):
        adapter, session, _ = _signed_in(
            adapter_factory, lambda: error_response(["Необходимо ввести имя пользователя"], 401)
        )

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.get_issue(session.sealed_token, DEMAND_KEY)

        assert exc.value.code == CODE_NEEDS_REAUTHORIZATION
        assert exc.value.needs_reauthorization is True
        assert exc.value.jira_unavailable is False

    def test_no_permission_during_read_is_not_reauthorization(self, adapter_factory):
        adapter, session, _ = _signed_in(
            adapter_factory,
            lambda: error_response(
                ["Вы не авторизованы. Для выполнения этой операции требуется аутентификация."], 401
            ),
        )

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.get_issue(session.sealed_token, DEMAND_KEY)

        assert exc.value.code == CODE_NO_PERMISSION
        assert exc.value.needs_reauthorization is False

    def test_jira_down_during_read_is_unavailable_not_reauthorization(self, adapter_factory):
        def handler(request):
            if request.url.path == ENDPOINT:
                return session_response()
            raise httpx.ConnectError("connection refused")

        adapter = adapter_factory(handler)
        session = adapter.login(PROBE_USER, PROBE_PASSWORD)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.get_issue(session.sealed_token, DEMAND_KEY)

        assert exc.value.code == CODE_JIRA_UNAVAILABLE
        assert exc.value.needs_reauthorization is False
        assert exc.value.retryable is True

    def test_no_httpx_exception_escapes_the_port(self, adapter_factory):
        """Приложение не должно знать про httpx — только про карту ошибок."""

        def handler(request):
            raise httpx.ConnectError("connection refused")

        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, PROBE_PASSWORD)
        assert not isinstance(exc.value, httpx.HTTPError)
        assert exc.value.code == CODE_JIRA_UNAVAILABLE

        # ``check_session`` не бросает вообще: возвращает состояние.
        tampered = base64.b64encode(b"y" * 32).decode("ascii")
        assert adapter.check_session(tampered).state == SESSION_NEEDS_REAUTHORIZATION


# ---------------------------------------------------------------------------
# 6. Секреты
# ---------------------------------------------------------------------------


class TestNoSecrets:
    def test_adapter_repr_and_describe_hide_everything(self, adapter_factory):
        handler = router(**login_routes())
        adapter = adapter_factory(handler)
        session = adapter.login(PROBE_USER, PROBE_PASSWORD)

        for text in (
            repr(adapter),
            str(adapter.describe()),
            repr(session),
            str(session.describe()),
            repr(handler.calls),
        ):
            assert PROBE_PASSWORD not in text
            assert FAKE_RAW_TOKEN not in text
            assert PROBE_USER not in text

    def test_config_repr_hides_token_and_password(self, make_config, adapter_factory):
        handler = router(**login_routes())
        config = make_config(
            session_token=FAKE_RAW_TOKEN,
            username=PROBE_USER,
            password=PROBE_PASSWORD,
        )
        adapter = adapter_factory(handler, config=config)

        text = repr(config)
        assert FAKE_RAW_TOKEN not in text
        assert PROBE_PASSWORD not in text
        assert "session_token_set=True" in text
        assert PROBE_PASSWORD not in str(adapter.describe())

    def test_error_carries_code_but_not_credentials(self, adapter_factory):
        handler = router(
            default=lambda: error_response(
                ["Ошибка доступа"], 401, {"x-seraph-loginreason": "AUTHENTICATED_FAILED"}
            )
        )
        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, PROBE_PASSWORD)

        assert PROBE_PASSWORD not in str(exc.value)
        assert PROBE_PASSWORD not in repr(exc.value.describe())
        assert exc.value.describe()["code"] == CODE_AUTH_FAILED
