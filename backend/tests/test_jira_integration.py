# -*- coding: utf-8 -*-
"""Адаптер Jira: вход, проверка сессии, чтение, карта ошибок (Dev-18).

Все проверки идут через ``httpx.MockTransport`` — сеть не открывается.
Фикстуры обезличены: реальных ключей задач, логинов, паролей и токенов здесь нет.
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
from datetime import datetime, timezone

import httpx
import pytest

from app.integrations.jira import (
    CODE_AUTH_FAILED,
    CODE_JIRA_ERROR,
    CODE_JIRA_UNAVAILABLE,
    CODE_LOGIN_BLOCKED,
    CODE_NEEDS_REAUTHORIZATION,
    CODE_NO_PERMISSION,
    OPERATIONAL_CODES,
    SESSION_JIRA_UNAVAILABLE,
    SESSION_NEEDS_REAUTHORIZATION,
    SESSION_NO_PERMISSION,
    SESSION_OK,
    JiraIntegrationError,
    JiraPort,
)

from .conftest import FAKE_RAW_TOKEN, error_response, response, session_response

ENDPOINT = "/rest/auth/1/session"
MYSELF_PATH = "/rest/api/2/myself"
SEARCH_PATH = "/rest/api/2/search"

#: Обезличенные данные: значатся только в этом тесте.
PROBE_USER = "probe-user"
FAKE_PROBE_PASSWORD = "FAKE_temporary-login-password"

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

        session = adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

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

        session = adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        body = json.loads(request_to(handler, ENDPOINT).content.decode("utf-8"))
        assert set(body) == {"username", "password"}
        assert body["password"] == FAKE_PROBE_PASSWORD

        for text in (repr(session), str(session.describe()), str(adapter.describe())):
            assert FAKE_PROBE_PASSWORD not in text
            assert FAKE_RAW_TOKEN not in text

    def test_wrong_password_is_auth_failed(self, adapter_factory):
        handler = router(
            default=lambda: error_response(
                ["Ошибка доступа"], 401, {"x-seraph-loginreason": "AUTHENTICATED_FAILED"}
            )
        )
        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        assert exc.value.code == CODE_AUTH_FAILED
        assert FAKE_PROBE_PASSWORD not in str(exc.value)

    def test_captcha_is_login_blocked(self, adapter_factory):
        handler = router(
            default=lambda: error_response(
                ["Проверка не пройдена"], 401, {"x-seraph-loginreason": "AUTHENTICATION_DENIED"}
            )
        )
        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        assert exc.value.code == CODE_LOGIN_BLOCKED
        assert exc.value.code != CODE_AUTH_FAILED
        # FR-029 (KON-69): текст из JSON-ответа Jira показывается как есть
        assert exc.value.message == "Проверка не пройдена"

    def test_blocked_without_jira_text_uses_generic_hint(self, adapter_factory):
        handler = router(
            default=lambda: error_response(
                [], 403, {"x-seraph-loginreason": "AUTHENTICATION_DENIED"}
            )
        )
        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        assert exc.value.code == CODE_LOGIN_BLOCKED
        assert "браузер" in exc.value.message

    def test_html_refusal_gives_hint_not_markup(self, adapter_factory):
        def html():
            return httpx.Response(
                403,
                text="<html><head><title>Denied</title></head><body>secret-page</body></html>",
                headers={"content-type": "text/html", "x-seraph-loginreason": "AUTHENTICATION_DENIED"},
            )

        adapter = adapter_factory(router(default=html))

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        assert exc.value.code == CODE_LOGIN_BLOCKED
        assert "<" not in exc.value.message and "secret-page" not in exc.value.message
        assert "браузер" in exc.value.message

    def test_password_echo_in_refusal_is_masked(self, adapter_factory):
        handler = router(
            default=lambda: error_response(
                ["Login failed for %s" % FAKE_PROBE_PASSWORD],
                403,
                {"x-seraph-loginreason": "AUTHENTICATION_DENIED"},
            )
        )
        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        assert FAKE_PROBE_PASSWORD not in exc.value.message
        assert "[скрыто]" in exc.value.message

    def test_unreachable_jira_is_jira_unavailable_not_wrong_password(self, adapter_factory):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        assert exc.value.code == CODE_JIRA_UNAVAILABLE
        assert exc.value.code != CODE_AUTH_FAILED
        assert exc.value.retryable is True

    def test_timeout_is_jira_unavailable_not_wrong_password(self, adapter_factory):
        def handler(request):
            raise httpx.ReadTimeout("read timed out")

        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        assert exc.value.code == CODE_JIRA_UNAVAILABLE
        # KON-119: без ведущего «: » и без внутреннего ID требования
        assert exc.value.message.startswith("Превышено время ожидания ответа Jira")
        assert "NFR" not in exc.value.message
        assert not exc.value.message.startswith(":")

    def test_server_error_is_jira_unavailable(self, adapter_factory):
        handler = router(default=lambda: error_response(["ошибка"], 500))
        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        assert exc.value.code == CODE_JIRA_UNAVAILABLE


# ---------------------------------------------------------------------------
# 2. check_session
# ---------------------------------------------------------------------------


class TestCheckSession:
    @staticmethod
    def _signed_in(adapter_factory, read):
        handler = router(default=read, **login_routes())
        adapter = adapter_factory(handler)
        session = adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)
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
        session = adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        check = adapter.check_session(session.sealed_token)

        assert check.state == SESSION_JIRA_UNAVAILABLE
        assert check.needs_reauthorization is False

    def test_timeout_is_not_reauthorization(self, adapter_factory):
        def handler(request):
            if request.url.path == ENDPOINT:
                return session_response()
            raise httpx.ReadTimeout("read timed out")

        adapter = adapter_factory(handler)
        session = adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

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
    session = adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)
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
        payload = {
            "key": DEMAND_KEY,
            "fields": {"summary": "запрос", "status": {"name": "Открыта"}},
        }
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
        session = adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

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
            adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)
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
        session = adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        for text in (
            repr(adapter),
            str(adapter.describe()),
            repr(session),
            str(session.describe()),
            repr(handler.calls),
        ):
            assert FAKE_PROBE_PASSWORD not in text
            assert FAKE_RAW_TOKEN not in text
            assert PROBE_USER not in text

    def test_config_repr_hides_token_and_password(self, make_config, adapter_factory):
        handler = router(**login_routes())
        config = make_config(
            session_token=FAKE_RAW_TOKEN,
            username=PROBE_USER,
            password=FAKE_PROBE_PASSWORD,
        )
        adapter = adapter_factory(handler, config=config)

        text = repr(config)
        assert FAKE_RAW_TOKEN not in text
        assert FAKE_PROBE_PASSWORD not in text
        assert "session_token_set=True" in text
        assert FAKE_PROBE_PASSWORD not in str(adapter.describe())

    def test_error_carries_code_but_not_credentials(self, adapter_factory):
        handler = router(
            default=lambda: error_response(
                ["Ошибка доступа"], 401, {"x-seraph-loginreason": "AUTHENTICATED_FAILED"}
            )
        )
        adapter = adapter_factory(handler)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)

        assert FAKE_PROBE_PASSWORD not in str(exc.value)
        assert FAKE_PROBE_PASSWORD not in repr(exc.value.describe())
        assert exc.value.describe()["code"] == CODE_AUTH_FAILED


# ---------------------------------------------------------------------------
# 7. Запись (KON-42): публичные методы порта поверх JiraClient
# ---------------------------------------------------------------------------

TRANSITIONS_PATH = "/rest/api/2/issue/%s/transitions" % DEMAND_KEY
ISSUE_PATH = "/rest/api/2/issue/%s" % DEMAND_KEY
ATTACHMENTS_PATH = "/rest/api/2/issue/%s/attachments" % DEMAND_KEY
COMMENT_PATH = "/rest/api/2/issue/%s/comment" % DEMAND_KEY

#: Обезличенный ответ ``expand=transitions.fields``: переход 21 и 171.
TRANSITIONS_PAYLOAD = {
    "transitions": [
        {
            "id": "21",
            "name": "Кандидат назначен",
            "to": {"id": "10", "name": "Кандидат назначен"},
            "fields": {
                "customfield_10707": {
                    "required": True,
                    "name": "Профиль сотрудника",
                    "fieldId": "customfield_10707",
                }
            },
        },
        {
            "id": "171",
            "name": "Кандидатов нет",
            "to": {"id": "12", "name": "Кандидатов нет"},
            "fields": {
                "customfield_16100": {
                    "required": True,
                    "name": "Причина отсутствия кандидата",
                    "fieldId": "customfield_16100",
                    "allowedValues": [
                        {"id": "23300", "value": "Не подходит по ставке"},
                        {"id": "23301", "value": "Другое"},
                    ],
                }
            },
        },
    ]
}


def _positional_kinds(callable_) -> list:
    """Виды позиционных параметров: имена у порта и assign разные, важна позиция."""
    kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return [p.kind for p in inspect.signature(callable_).parameters.values() if p.kind in kinds]


class TestWriteTransitions:
    def test_get_transitions_maps_fields_and_allowed_values(self, adapter_factory):
        adapter, session, _ = _signed_in(
            adapter_factory, lambda: response(TRANSITIONS_PAYLOAD)
        )

        transitions = adapter.get_transitions(session.sealed_token, DEMAND_KEY)

        by_id = {t.id: t for t in transitions}
        assert set(by_id) == {"21", "171"}
        assert by_id["21"].to_status == "Кандидат назначен"
        reason = by_id["171"].fields[0]
        assert reason.field_id == "customfield_16100"
        assert reason.required is True
        assert [v.id for v in reason.allowed_values] == ["23300", "23301"]
        assert reason.allowed_values[0].name == "Не подходит по ставке"

    def test_do_transition_sends_comment_through_update(self, adapter_factory):
        adapter, session, handler = _signed_in(adapter_factory, lambda: response(None, 204))

        adapter.do_transition(
            session.sealed_token,
            DEMAND_KEY,
            "171",
            fields={"customfield_16100": {"id": "23300"}},
            comment="причина",
        )

        request = handler.calls[-1]
        assert request.method == "POST" and request.url.path == TRANSITIONS_PATH
        assert json.loads(request.content) == {
            "transition": {"id": "171"},
            "fields": {"customfield_16100": {"id": "23300"}},
            "update": {"comment": [{"add": {"body": "причина"}}]},
        }

    def test_do_transition_without_comment_sends_no_update(self, adapter_factory):
        adapter, session, handler = _signed_in(adapter_factory, lambda: response(None, 204))

        adapter.do_transition(
            session.sealed_token, DEMAND_KEY, "21", fields={"assignee": {"name": "u"}}
        )

        body = json.loads(handler.calls[-1].content)
        assert body == {"transition": {"id": "21"}, "fields": {"assignee": {"name": "u"}}}

    def test_do_transition_does_not_recheck_transitions(self, adapter_factory):
        """validate=False: доступность перехода уже проверена — один вызов (NFR-009)."""
        adapter, session, handler = _signed_in(adapter_factory, lambda: response(None, 204))
        before = len(handler.calls)

        adapter.do_transition(session.sealed_token, DEMAND_KEY, "21", fields={})

        assert len(handler.calls) - before == 1
        assert handler.calls[-1].url.path == TRANSITIONS_PATH


class TestWriteFieldsAndFiles:
    def test_set_fields_puts_fields(self, adapter_factory):
        adapter, session, handler = _signed_in(adapter_factory, lambda: response(None, 204))

        adapter.set_fields(session.sealed_token, DEMAND_KEY, {"customfield_20809": 3000})

        request = handler.calls[-1]
        assert request.method == "PUT" and request.url.path == ISSUE_PATH
        assert json.loads(request.content) == {"fields": {"customfield_20809": 3000}}

    def test_assign_candidate_puts_assignee_by_login(self, adapter_factory):
        adapter, session, handler = _signed_in(adapter_factory, lambda: response(None, 204))

        adapter.assign_candidate(session.sealed_token, DEMAND_KEY, "candidate-login")

        assert json.loads(handler.calls[-1].content) == {
            "fields": {"assignee": {"name": "candidate-login"}}
        }

    def test_add_comment_posts_body(self, adapter_factory):
        adapter, session, handler = _signed_in(
            adapter_factory, lambda: response({"id": "1", "body": "т"}, 201)
        )

        adapter.add_comment(session.sealed_token, DEMAND_KEY, "текст")

        request = handler.calls[-1]
        assert request.method == "POST" and request.url.path == COMMENT_PATH
        assert json.loads(request.content) == {"body": "текст"}

    def test_attach_file_sends_no_check_header(self, adapter_factory):
        adapter, session, handler = _signed_in(
            adapter_factory, lambda: response([{"id": "1", "filename": "cv.docx"}], 200)
        )

        adapter.attach_file(
            session.sealed_token, DEMAND_KEY, "cv.docx", b"abc", "application/msword"
        )

        request = handler.calls[-1]
        assert request.method == "POST" and request.url.path == ATTACHMENTS_PATH
        assert request.headers["X-Atlassian-Token"] == "no-check"
        assert b"cv.docx" in request.content


class TestWriteRefusals:
    def test_expired_session_during_write_is_needs_reauthorization(self, adapter_factory):
        adapter, session, _ = _signed_in(
            adapter_factory, lambda: error_response(["Необходимо ввести имя пользователя"], 401)
        )

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.do_transition(session.sealed_token, DEMAND_KEY, "21", fields={})

        assert exc.value.code == CODE_NEEDS_REAUTHORIZATION
        assert exc.value.needs_reauthorization is True

    def test_jira_text_is_kept_verbatim(self, adapter_factory):
        adapter, session, _ = _signed_in(
            adapter_factory, lambda: error_response(["Поле обязательно"], 400)
        )

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.set_fields(session.sealed_token, DEMAND_KEY, {"customfield_16100": {"id": "1"}})

        assert exc.value.code == CODE_JIRA_ERROR
        assert "Поле обязательно" in exc.value.message

    def test_empty_comment_is_request_error_without_call(self, adapter_factory):
        adapter, session, handler = _signed_in(adapter_factory, lambda: response(None, 204))
        before = len(handler.calls)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.add_comment(session.sealed_token, DEMAND_KEY, "   ")

        assert exc.value.code == CODE_JIRA_ERROR
        assert len(handler.calls) == before

    def test_empty_fields_is_request_error_without_call(self, adapter_factory):
        adapter, session, handler = _signed_in(adapter_factory, lambda: response(None, 204))
        before = len(handler.calls)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.set_fields(session.sealed_token, DEMAND_KEY, {})

        assert exc.value.code == CODE_JIRA_ERROR
        assert len(handler.calls) == before


class TestWritePortShape:
    """Адаптер реализует публичный ``JiraPort`` целиком; узкого порта assign больше нет (KON-97)."""

    def test_adapter_satisfies_jira_port(self, adapter_factory):
        adapter = adapter_factory(router(**login_routes()))
        assert isinstance(adapter, JiraPort)

    def test_candidate_assignment_is_exposed(self, adapter_factory):
        adapter = adapter_factory(router(**login_routes()))
        assert callable(getattr(adapter, "assign_candidate", None))


class TestWriteNoSecrets:
    def test_write_logs_carry_no_secrets(self, adapter_factory, caplog):
        adapter, session, _ = _signed_in(adapter_factory, lambda: response(None, 204))

        with caplog.at_level(logging.DEBUG):
            adapter.do_transition(
                session.sealed_token, DEMAND_KEY, "21",
                fields={"assignee": {"name": "u"}}, comment="к",
            )
            adapter.add_comment(session.sealed_token, DEMAND_KEY, "к")

        text = "\n".join(record.getMessage() for record in caplog.records)
        assert FAKE_RAW_TOKEN not in text
        assert FAKE_PROBE_PASSWORD not in text
        assert session.sealed_token not in text


# ---------------------------------------------------------------------------
# 8. Ставка кнопкой «Ставка» (ScriptRunner, KON-99, CR-008): set_rate
# ---------------------------------------------------------------------------

RATE_PATH = "/rest/scriptrunner/latest/custom/rateCalculationLogic"
RATE_KW = dict(amount="2700", vat="20999", currency="21001", unit="21006")


def _rate_issue(amount=2700.0, vat="20999", currency="21001", unit="21006"):
    return lambda: response({"key": DEMAND_KEY, "fields": {
        "customfield_20809": amount,
        "customfield_20810": {"id": vat, "value": "Да"},
        "customfield_20811": {"id": currency, "value": "руб."},
        "customfield_20812": {"id": unit, "value": "ч (час)"},
    }})


def _rate_adapter(adapter_factory, *, post=lambda: response(None, 200), issue=None):
    routes = {RATE_PATH: post, ISSUE_PATH: issue or _rate_issue(), **login_routes()}
    handler = router(default=MYSELF_OK, **routes)
    adapter = adapter_factory(handler)
    session = adapter.login(PROBE_USER, FAKE_PROBE_PASSWORD)
    return adapter, session, handler


def _posts(handler):
    return [r for r in handler.calls if r.url.path == RATE_PATH]


class TestSetRate:
    def test_body_format_matches_probe(self, adapter_factory):
        adapter, session, handler = _rate_adapter(adapter_factory)

        adapter.set_rate(session.sealed_token, DEMAND_KEY, username="oper", **RATE_KW)

        request = _posts(handler)[0]
        assert request.method == "POST"
        assert request.headers["content-type"].startswith("application/json")
        assert json.loads(request.content) == {
            "issueKey": DEMAND_KEY,
            "currentUserUsername": "oper",
            "customfield_21105": "22412",
            "customfield_20809": "2700",
            "customfield_20810": "20999",
            "customfield_20811": "21001",
            "customfield_20812": "21006",
        }

    def test_amount_is_number_string_and_login_from_myself_by_default(self, adapter_factory):
        adapter, session, handler = _rate_adapter(
            adapter_factory, issue=_rate_issue(amount=2700.5)
        )

        adapter.set_rate(session.sealed_token, DEMAND_KEY, **{**RATE_KW, "amount": 2700.50})

        body = json.loads(_posts(handler)[0].content)
        assert body["customfield_20809"] == "2700.5"
        assert body["currentUserUsername"] == PROBE_USER
        assert body["customfield_21105"] == "22412"       # передаётся всегда

    def test_session_cookie_is_used_and_no_secrets_leak(self, adapter_factory, caplog):
        adapter, session, handler = _rate_adapter(adapter_factory)

        with caplog.at_level(logging.DEBUG):
            adapter.set_rate(session.sealed_token, DEMAND_KEY, username="oper", **RATE_KW)

        assert FAKE_RAW_TOKEN in _posts(handler)[0].headers.get("cookie", "")
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert FAKE_RAW_TOKEN not in text and FAKE_PROBE_PASSWORD not in text
        assert FAKE_RAW_TOKEN not in _posts(handler)[0].content.decode()

    @pytest.mark.parametrize("kw", [
        {"vat": "888888"}, {"currency": "999999"}, {"unit": "1"}, {"proposer": "22411"},
        {"proposer": "777777"}, {"proposer": ""},
    ])
    def test_unknown_reference_ids_rejected_before_send(self, adapter_factory, kw):
        adapter, session, handler = _rate_adapter(adapter_factory)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **{**RATE_KW, **kw})

        assert exc.value.code == CODE_JIRA_ERROR
        assert _posts(handler) == []

    @pytest.mark.parametrize("amount", [None, "", "  ", "abc", "-5", float("nan")])
    def test_empty_or_bad_amount_is_not_sent(self, adapter_factory, amount):
        adapter, session, handler = _rate_adapter(adapter_factory)

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.set_rate(
                session.sealed_token, DEMAND_KEY, username="o", **{**RATE_KW, "amount": amount}
            )

        assert exc.value.code == CODE_JIRA_ERROR
        assert _posts(handler) == []

    def test_200_without_write_is_detected_by_reread(self, adapter_factory):
        adapter, session, handler = _rate_adapter(
            adapter_factory, issue=_rate_issue(amount=2500.0)   # Jira ответила 200, значение старое
        )

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **RATE_KW)

        assert exc.value.code == CODE_JIRA_ERROR
        assert "не записана" in exc.value.message
        assert len(_posts(handler)) == 1

    def test_reread_detects_ignored_reference_value(self, adapter_factory):
        adapter, session, _ = _rate_adapter(adapter_factory, issue=_rate_issue(currency="21002"))

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **RATE_KW)

        assert exc.value.code == CODE_JIRA_ERROR

    def test_missing_amount_in_reread_is_failure(self, adapter_factory):
        adapter, session, _ = _rate_adapter(adapter_factory, issue=_rate_issue(amount=None))

        with pytest.raises(JiraIntegrationError):
            adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **RATE_KW)

    def test_500_is_request_error_not_unavailable(self, adapter_factory):
        adapter, session, _ = _rate_adapter(
            adapter_factory,
            post=lambda: response({"message": "NullPointerException"}, 500),
        )

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.set_rate(session.sealed_token, "ANALYTICS-999999999", username="o", **RATE_KW)

        assert exc.value.code == CODE_JIRA_ERROR
        assert exc.value.retryable is False and exc.value.status == 500

    def test_503_stays_unavailable(self, adapter_factory):
        adapter, session, _ = _rate_adapter(adapter_factory, post=lambda: response(None, 503))

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **RATE_KW)

        assert exc.value.code == CODE_JIRA_UNAVAILABLE

    @pytest.mark.parametrize("status,headers,code", [
        (401, {"x-seraph-loginreason": "AUTHENTICATED_FAILED"}, CODE_NEEDS_REAUTHORIZATION),
        (403, {}, "no_permission"),
    ])
    def test_401_403_as_before(self, adapter_factory, status, headers, code):
        adapter, session, _ = _rate_adapter(
            adapter_factory,
            post=lambda: error_response(["отказ"], status, headers),
        )

        with pytest.raises(JiraIntegrationError) as exc:
            adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **RATE_KW)

        assert exc.value.code == code

    def test_port_exposes_set_rate_with_contract_signature(self, adapter_factory):
        adapter = adapter_factory(router(**login_routes()))
        params = inspect.signature(adapter.set_rate).parameters
        assert isinstance(adapter, JiraPort)
        assert params["proposer"].default == "22412"
        for name in ("amount", "vat", "currency", "unit"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
