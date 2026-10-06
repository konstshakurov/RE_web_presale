# -*- coding: utf-8 -*-
"""Чтение через клиента: JQL, пагинация, Agile API, переходы, кэш метаданных."""

from __future__ import annotations

import json

import httpx
import pytest

from jira_client.client import JiraClient, jql_quote
from jira_client.errors import (
    JiraAuthError,
    JiraBadRequestError,
    JiraNotFoundError,
    JiraTimeoutError,
    JiraUnavailableError,
)
from tests.conftest import (
    AUTH_OK_BODY,
    FAKE_TOKEN,
    NO_ACCESS_BODY,
    NO_AUTH_BODY,
    NO_PERMISSION_BODY,
    NOT_FOUND_BODY,
    html_response,
    json_response,
)


def _ok(payload, status: int = 200):
    """Ответ 200 с JSON-телом. ``status`` нужен для проверки кодов ошибок."""
    return lambda request: json_response(payload, status=status)


def _search_page(keys, total, start_at):
    return {
        "startAt": start_at,
        "maxResults": len(keys),
        "total": total,
        "issues": [{"key": k, "fields": {"summary": k}} for k in keys],
    }


class TestServerInfo:
    def test_returns_version(self, client_factory):
        client, calls = client_factory(
            _ok({"baseUrl": "https://jira.example.internal", "version": "8.13.0",
                 "deploymentType": "Server"})
        )
        info = client.server_info()
        assert info.version == "8.13.0"
        assert calls[0].path == "/rest/api/2/serverInfo"
        assert calls[0].method == "GET"


class TestSessionCheck:
    def test_ok(self, client_factory):
        client, _ = client_factory(_ok(AUTH_OK_BODY))
        result = client.check_session()
        assert result["ok"] is True
        assert result["authenticated"] is True
        assert result["needs_reauthorization"] is False

    def test_expired_session_is_reported_not_raised(self, client_factory):
        """FR-029: 401 должен распознаваться, а не падать исключением наружу."""
        client, _ = client_factory(_ok(NO_AUTH_BODY, status=401))
        result = client.check_session()
        assert result["ok"] is False
        assert result["status"] == 401
        assert result["reason"] == "no_credentials"
        assert result["needs_reauthorization"] is True

    def test_permission_denial_is_distinguished(self, client_factory):
        """«Нет прав» не должно выглядеть как «сессия истекла»."""
        client, _ = client_factory(_ok(NO_PERMISSION_BODY, status=401))
        result = client.check_session()
        assert result["reason"] == "no_permission"
        assert result["needs_reauthorization"] is False

    def test_permission_denial_on_403(self, client_factory):
        client, _ = client_factory(_ok({}, status=403))
        result = client.check_session()
        assert result["ok"] is False
        assert result["status"] == 403
        assert result["needs_reauthorization"] is False

    def test_login_required_is_distinguished(self, client_factory):
        client, _ = client_factory(_ok(NO_ACCESS_BODY, status=401))
        result = client.check_session()
        assert result["reason"] == "login_required"
        assert result["needs_reauthorization"] is True

    def test_html_401_is_compressed(self, client_factory):
        """Jira отдаёт 13 КБ HTML; в отчёт это уходить не должно."""
        client, _ = client_factory(lambda request: html_response("Unauthorized (401)"))
        result = client.check_session()
        assert result["reason"] == "invalid_cookie_or_expired"
        assert len(result["summary"]) < 250
        assert "<html>" not in result["summary"]

    def test_server_error_does_not_ask_reauthorization(self, client_factory):
        client, _ = client_factory(_ok({"message": "сбой"}, status=500))
        result = client.check_session()
        assert result["ok"] is False
        assert result["reason"] == "error"
        assert result["needs_reauthorization"] is False


class TestCookieIsSent:
    def test_cookie_header_present(self, client_factory):
        client, calls = client_factory(_ok(AUTH_OK_BODY))
        client.myself()
        assert calls[0].header("Cookie") == "JSESSIONID=" + FAKE_TOKEN


class TestGetIssue:
    def test_requests_narrow_field_list(self, client_factory, sample_issue):
        client, calls = client_factory(_ok(sample_issue))
        client.get_issue("ANALYTICS-0001")
        requested = calls[0].params["fields"].split(",")
        # FR-028: запрашивать только нужные поля, а не все 625-666.
        assert "summary" in requested
        assert "customfield_10101" in requested
        assert len(requested) < 40
        assert len(requested) == len(set(requested))

    def test_get_demand_maps_model(self, client_factory, sample_issue):
        client, _ = client_factory(_ok(sample_issue))
        demand = client.get_demand("ANALYTICS-0001")
        assert demand.epic_key == "SA-0001"
        assert demand.status.category_key == "new"

    def test_404_raises_not_found(self, client_factory):
        client, _ = client_factory(_ok(NOT_FOUND_BODY, status=404))
        try:
            client.get_issue("ANALYTICS-0000")
            raise AssertionError("ожидался JiraNotFoundError")
        except JiraNotFoundError as exc:
            assert exc.status == 404

    def test_401_raises_auth_error(self, client_factory):
        client, _ = client_factory(_ok(NO_AUTH_BODY, status=401))
        try:
            client.get_issue("ANALYTICS-0001")
            raise AssertionError("ожидался JiraAuthError")
        except JiraAuthError:
            pass


class TestSearch:
    def test_returns_page(self, client_factory):
        client, calls = client_factory(_ok(_search_page(["A-1", "A-2"], 282, 0)))
        page = client.search("project = ANALYTICS", max_results=100, start_at=0)
        assert page.total == 282
        assert len(page.issues) == 2
        assert calls[0].params["jql"] == "project = ANALYTICS"
        assert calls[0].params["maxResults"] == "100"

    def test_max_results_is_capped_at_page_size(self, client_factory):
        """Проверено пробой: над 100 Jira начинает усекать выдачу."""
        client, calls = client_factory(_ok(_search_page([], 0, 0)))
        client.search("project = ANALYTICS", max_results=5000)
        assert int(calls[0].params["maxResults"]) == 100

    def test_bad_jql_raises_bad_request(self, client_factory):
        client, _ = client_factory(
            _ok({"errorMessages": ["Ошибка в запросе JQL: ..."], "errors": {}}, status=400)
        )
        try:
            client.search("это {не} jql")
            raise AssertionError("ожидался JiraBadRequestError")
        except JiraBadRequestError as exc:
            assert "JQL" in exc.summary


class TestSearchPaged:
    def test_walks_all_pages(self, client_factory):
        total = 3
        pages = {0: ["A-1", "A-2"], 2: ["A-3"]}

        def handler(request):
            start = int(request.url.params.get("startAt", 0))
            keys = pages.get(start, [])
            return json_response(
                {
                    "startAt": start,
                    "maxResults": 2,
                    "total": total,
                    "issues": [{"key": k, "fields": {}} for k in keys],
                }
            )

        client, calls = client_factory(handler)
        # page_size меньше общего числа задач — обход обязан перейти на вторую страницу.
        keys = [issue["key"] for issue in client.search_paged("project = ANALYTICS", page_size=2)]
        assert keys == ["A-1", "A-2", "A-3"]
        assert [c.params["startAt"] for c in calls] == ["0", "2"]

    def test_empty_first_page_stops(self, client_factory):
        client, calls = client_factory(_ok(_search_page([], 0, 0)))
        assert list(client.search_paged("project = ANALYTICS")) == []
        assert len(calls) == 1

    def test_max_pages_guard(self, client_factory):
        client, calls = client_factory(
            _ok({"startAt": 0, "total": 10_000, "issues": [{"key": "A-1", "fields": {}}]})
        )
        list(client.search_paged("project = ANALYTICS", page_size=100, max_pages=3))
        assert len(calls) == 3


class TestIncrementalSearch:
    def test_jql_shape(self, client_factory):
        """Регрессия: ``ORDER BY`` не должен склеиваться через `` AND `` —
        Jira 8.13 отклоняет такой JQL кодом 400 (найдено живой пробой)."""
        client, calls = client_factory(_ok(_search_page([], 0, 0)))
        list(client.search_updated_since("2026-10-05 09:00"))
        jql = calls[0].params["jql"]
        assert jql == (
            'project = ANALYTICS AND updated > "2026-10-05 09:00" '
            "ORDER BY updated ASC, key ASC"
        )
        assert " AND ORDER BY" not in jql

    def test_issuetype_filter_is_optional(self, client_factory):
        client, calls = client_factory(_ok(_search_page([], 0, 0)))
        list(client.search_updated_since("2026-10-05 09:00", issuetype="Demand"))
        assert calls[0].params["jql"] == (
            "project = ANALYTICS AND issuetype = Demand AND updated > \"2026-10-05 09:00\" "
            "ORDER BY updated ASC, key ASC"
        )

    def test_ordering_by_updated_is_mandatory(self, client_factory):
        """Без сортировки инкрементальный обход теряет задачи."""
        client, calls = client_factory(_ok(_search_page([], 0, 0)))
        list(client.search_updated_since("2026-10-05 09:00"))
        assert "ORDER BY updated ASC, key ASC" in calls[0].params["jql"]


class TestJqlQuote:
    def test_plain(self):
        assert jql_quote("2026-10-05 09:00") == '"2026-10-05 09:00"'

    def test_escapes_quotes(self):
        assert jql_quote('a"b') == '"a\\"b"'

    def test_escapes_backslash(self):
        assert jql_quote("a\\b") == '"a\\\\b"'


class TestEpicChildren:
    def test_uses_agile_endpoint(self, client_factory):
        client, calls = client_factory(_ok({"total": 282, "issues": []}))
        client.epic_children("SA-0001")
        assert calls[0].path == "/rest/agile/1.0/epic/SA-0001/issue"
        assert calls[0].params["maxResults"] == "100"
        assert calls[0].params["startAt"] == "0"

    def test_extra_jql_is_forwarded(self, client_factory):
        client, calls = client_factory(_ok({"total": 0, "issues": []}))
        client.epic_children("SA-0001", extra_jql='status != "Done"')
        assert calls[0].params["jql"] == 'status != "Done"'

    def test_iter_walks_pages(self, client_factory):
        def handler(request):
            start = int(request.url.params.get("startAt", 0))
            payload = (
                {"total": 2, "issues": [{"key": "A-1", "fields": {}}]} if start == 0
                else {"total": 2, "issues": [{"key": "A-2", "fields": {}}]}
            )
            return json_response(payload)

        client, calls = client_factory(handler)
        keys = [i["key"] for i in client.iter_epic_children("SA-0001")]
        assert keys == ["A-1", "A-2"]
        assert len(calls) == 2

    def test_child_total_is_authoritative(self, client_factory):
        """Проба: total из Agile API совпадает с JQL по Epic Link."""
        client, _ = client_factory(_ok({"total": 282, "issues": []}))
        page = client.epic_children("SA-0001")
        assert page.total == 282
        assert page.is_last is True


class TestGetEpic:
    """``GET /rest/agile/1.0/epic/{key}`` — сам эпик (Dev-18)."""

    def test_uses_agile_endpoint(self, client_factory):
        client, calls = client_factory(_ok({"id": "1", "key": "SA-0001"}))
        client.get_epic("SA-0001")
        assert calls[0].path == "/rest/agile/1.0/epic/SA-0001"

    def test_default_fields_are_epic_read_fields(self, client_factory):
        from jira_client import fields as F

        client, calls = client_factory(_ok({"key": "SA-0001"}))
        client.get_epic("SA-0001")
        assert calls[0].params["fields"] == ",".join(F.EPIC_READ_FIELDS)

    def test_field_list_is_configurable(self, client_factory):
        client, calls = client_factory(_ok({"key": "SA-0001"}))
        client.get_epic("SA-0001", fields=["summary", "status"])
        assert calls[0].params["fields"] == "summary,status"

    def test_missing_epic_raises_not_found(self, client_factory):
        client, _ = client_factory(_ok(NOT_FOUND_BODY, status=404))
        with pytest.raises(JiraNotFoundError):
            client.get_epic("SA-0001")

    def test_epic_link_field_is_canonical(self, client_factory):
        """Связь «эпик ↔ потребность» читается как customfield_10101 (FR-028)."""
        from jira_client import fields as F

        assert F.EPIC_LINK == "customfield_10101"
        client, calls = client_factory(_ok({"key": "SA-0001"}))
        client.get_epic("SA-0001", fields=["summary", F.EPIC_LINK])
        assert calls[0].params["fields"] == "summary,customfield_10101"


class TestTransitions:
    def _transitions(self):
        return {
            "expand": "transitions",
            "transitions": [
                {
                    "id": "31",
                    "name": "Кандидатов нет",
                    "to": {"id": "7", "name": "Кандидатов нет"},
                    "fields": [
                        {
                            "fieldId": "customfield_20904",
                            "name": "Причина отсутствия кандидата",
                            "required": True,
                            "allowedValues": [
                                {"id": "1", "name": "требуется очное присутствие"}
                            ],
                        }
                    ],
                },
                {"id": "11", "name": "Далее", "to": {"id": "3", "name": "В процессе"}},
            ],
        }

    def test_expand_is_requested(self, client_factory):
        """Без expand Jira не отдаёт поля перехода — FR-026 не выполнима."""
        client, calls = client_factory(_ok(self._transitions()))
        client.get_transitions("ANALYTICS-0001")
        assert calls[0].params["expand"] == "transitions.fields"

    def test_parses_required_fields(self, client_factory):
        client, _ = client_factory(_ok(self._transitions()))
        transitions = client.get_transitions("ANALYTICS-0001")
        assert len(transitions) == 2
        required = transitions[0].required_fields
        assert required[0].name == "Причина отсутствия кандидата"
        assert "требуется очное присутствие" in required[0].value_hint()

    def test_empty_transitions(self, client_factory):
        client, _ = client_factory(_ok({"transitions": []}))
        assert client.get_transitions("ANALYTICS-0001") == []

    def test_malformed_payload(self, client_factory):
        client, _ = client_factory(_ok({"transitions": "мусор"}))
        assert client.get_transitions("ANALYTICS-0001") == []

    def test_find_by_id(self, client_factory):
        client, _ = client_factory(_ok(self._transitions()))
        assert client.find_transition("ANALYTICS-0001", transition_id="31").name == \
            "Кандидатов нет"

    def test_find_by_target_status(self, client_factory):
        client, _ = client_factory(_ok(self._transitions()))
        found = client.find_transition("ANALYTICS-0001", to_status="Кандидатов нет")
        assert found.id == "31"

    def test_find_by_name_contains(self, client_factory):
        client, _ = client_factory(_ok(self._transitions()))
        found = client.find_transition("ANALYTICS-0001", name_contains="кандидатов")
        assert found.id == "31"

    def test_not_found_returns_none(self, client_factory):
        client, _ = client_factory(_ok({"transitions": []}))
        assert client.find_transition("ANALYTICS-0001", transition_id="999") is None


class TestMissingRequiredFields:
    """Проверка обязательных полей перехода до вызова Jira."""

    def _transition(self, required_ids=("a",)):
        from jira_client.models import FieldSpec, Reference, Transition

        return Transition(
            id="31",
            name="Кандидатов нет",
            to_status=Reference(id="7", name="Кандидатов нет"),
            fields=[
                FieldSpec(field_id=field_id, name="Поле %s" % field_id.upper(), required=True)
                for field_id in required_ids
            ],
        )

    def test_all_missing_when_nothing_provided(self, client_factory):
        client, _ = client_factory(_ok({"transitions": []}))
        assert client.missing_required_fields(self._transition(("a", "b"))) == ["a", "b"]

    def test_satisfied_by_value(self, client_factory):
        client, _ = client_factory(_ok({"transitions": []}))
        transition = self._transition()
        assert client.missing_required_fields(transition, {"a": "значение"}) == []

    def test_empty_values_do_not_satisfy_requirement(self, client_factory):
        """Jira сочла бы пустое значение незаполненным и вернула бы 400."""
        client, _ = client_factory(_ok({"transitions": []}))
        transition = self._transition()
        for empty in (None, "", [], {}):
            assert client.missing_required_fields(transition, {"a": empty}) == ["a"], empty

    def test_other_fields_do_not_satisfy_requirement(self, client_factory):
        client, _ = client_factory(_ok({"transitions": []}))
        assert client.missing_required_fields(self._transition(), {"b": "значение"}) == ["a"]

    def test_zero_is_a_valid_value(self, client_factory):
        client, _ = client_factory(_ok({"transitions": []}))
        assert client.missing_required_fields(self._transition(), {"a": 0}) == []

    def test_no_required_fields(self, client_factory):
        from jira_client.models import Transition

        client, _ = client_factory(_ok({"transitions": []}))
        assert client.missing_required_fields(Transition(id="1", name="Далее")) == []


class TestEditMeta:
    def test_returns_meta(self, client_factory):
        client, calls = client_factory(_ok({"fields": {"summary": {"required": True}}}))
        meta = client.get_editmeta("ANALYTICS-0001")
        assert "summary" in meta["fields"]
        assert calls[0].path == "/rest/api/2/issue/ANALYTICS-0001/editmeta"


class TestMetadataCaching:
    def test_fields_are_cached(self, client_factory):
        client, calls = client_factory(_ok([{"id": "summary", "name": "Сводка"}]))
        client.list_fields()
        client.list_fields()
        assert len(calls) == 1

    def test_refresh_bypasses_cache(self, client_factory):
        client, calls = client_factory(_ok([{"id": "summary", "name": "Сводка"}]))
        client.list_fields()
        client.list_fields(refresh=True)
        assert len(calls) == 2

    def test_statuses_are_cached(self, client_factory):
        client, calls = client_factory(_ok([{"id": "1", "name": "Открыт"}]))
        client.list_statuses()
        client.list_statuses()
        assert len(calls) == 1
        assert client.list_statuses()[0]["name"] == "Открыт"


class TestCommentsAndAttachments:
    def test_comments_are_read_from_issue(self, client_factory):
        payload = {
            "key": "ANALYTICS-0001",
            "fields": {
                "comment": {
                    "total": 1,
                    "comments": [
                        {"id": "1", "body": "Текст", "author": {"displayName": "Тест"}}
                    ],
                }
            },
        }
        client, calls = client_factory(_ok(payload))
        comments = client.get_comments("ANALYTICS-0001")
        assert len(comments) == 1
        assert comments[0].body == "Текст"
        # Отдельного эндпоинта комментариев нет — берём из задачи.
        assert calls[0].params["fields"] == "comment"

    def test_empty_comments(self, client_factory):
        client, _ = client_factory(_ok({"key": "X", "fields": {"comment": {}}}))
        assert client.get_comments("X") == []

    def test_attachments_are_read_from_issue(self, client_factory):
        payload = {
            "key": "ANALYTICS-0001",
            "fields": {
                "attachment": [
                    {"id": "5000", "filename": "резюме.pdf", "size": 100}
                ]
            },
        }
        client, calls = client_factory(_ok(payload))
        attachments = client.get_attachments("ANALYTICS-0001")
        assert attachments[0].filename == "резюме.pdf"
        assert calls[0].params["fields"] == "attachment"

    def test_empty_attachments(self, client_factory):
        client, _ = client_factory(_ok({"key": "X", "fields": {}}))
        assert client.get_attachments("X") == []

    def test_download_uses_secure_path(self, client_factory):
        """Проба: содержимое вложения лежит на /secure/attachment/, не в REST."""
        from jira_client.models import Attachment

        def handler(request):
            if request.url.path.startswith("/secure/attachment/"):
                return httpx.Response(200, content=b"%PDF-1.4 fake")
            return json_response({})

        client, calls = client_factory(handler)
        attachment = Attachment(id="5000", filename="резюме тест.pdf", size=13,
                                mime_type="application/pdf", created=None, url="x")
        content = client.download_attachment(attachment)
        assert content == b"%PDF-1.4 fake"
        # httpx декодирует путь, поэтому кириллица здесь читаема.
        assert calls[0].path.startswith("/secure/attachment/5000/")
        assert "резюме" in calls[0].path
        # А в сыром URL имя закодировано — иначе кириллица ломает запрос.
        assert "%D1%80%D0%B5%D0%B7%D1%8E%D0%BC%D0%B5" in calls[0].url

    def test_download_without_url_is_rejected(self, client_factory):
        from jira_client.errors import JiraBadRequestError
        from jira_client.models import Attachment

        client, calls = client_factory(_ok({}))
        attachment = Attachment(id="1", filename="a.pdf", size=1, mime_type=None,
                                created=None, url=None)
        with pytest.raises(JiraBadRequestError):
            client.download_attachment(attachment)
        assert calls == []


class TestHeaderEncoding:
    """httpx кодирует заголовки в latin-1; клиент обязан сказать об этом сам."""

    def test_non_ascii_token_gives_clear_error(self, client_factory):
        from tests.conftest import make_config

        client, calls = client_factory(
            _ok(AUTH_OK_BODY),
            config=make_config(session_token="не-ASCII-токен"),
        )
        with pytest.raises(ValueError) as exc:
            client.myself()
        assert "latin-1" in str(exc.value)
        assert calls == [], "запрос не должен уходить"

    def test_non_string_header_value_is_rejected(self, client_factory):
        client, _ = client_factory(_ok(AUTH_OK_BODY))
        with pytest.raises(TypeError):
            client.request("GET", "/rest/api/2/myself", headers={"Accept": 123})

    def test_ascii_token_passes(self, client_factory):
        client, calls = client_factory(_ok(AUTH_OK_BODY))
        client.myself()
        assert calls[0].header("Cookie", ) is not None


class TestDiagnose:
    def test_reports_each_check(self, client_factory):
        def handler(request):
            if request.url.path == "/rest/api/2/myself":
                return json_response(NO_AUTH_BODY, status=401)
            if request.url.path == "/rest/api/2/serverInfo":
                return json_response({"version": "8.13.0"})
            return json_response([])

        client, _ = client_factory(handler)
        report = client.diagnose()
        labels = [c["label"] for c in report["checks"]]
        assert labels == ["serverInfo", "myself", "field", "status"]
        by_label = {c["label"]: c for c in report["checks"]}
        assert by_label["serverInfo"]["ok"] is True
        assert by_label["myself"]["ok"] is False
        assert by_label["myself"]["status"] == 401

    def test_report_contains_no_secrets(self, client_factory):
        client, _ = client_factory(_ok({}))
        blob = json.dumps(client.diagnose(), ensure_ascii=False, default=str)
        assert FAKE_TOKEN not in blob
        assert "session_token" in blob  # только признак наличия


class TestErrorMappingInRequest:
    def test_timeout_raises_with_nfr009_text(self, client_factory):
        def handler(request):
            raise httpx.ReadTimeout("timed out", request=request)

        client, _ = client_factory(handler)
        try:
            client.myself()
            raise AssertionError("ожидался JiraTimeoutError")
        except JiraTimeoutError as exc:
            assert "NFR-009" in str(exc)
            assert exc.timeout == 10.0
            assert exc.retryable is True

    def test_network_error_raises_unavailable(self, client_factory):
        def handler(request):
            raise httpx.ConnectError("нет сети", request=request)

        client, _ = client_factory(handler)
        try:
            client.myself()
            raise AssertionError("ожидался JiraUnavailableError")
        except JiraUnavailableError as exc:
            assert exc.retryable is True

    def test_html_error_body_is_not_json_crash(self, client_factory):
        """Jira при неверной cookie отдаёт HTML на 13 КБ вместо JSON."""

        def handler(request):
            return httpx.Response(
                401,
                content=b"<html><head><title>Unauthorized (401)</title></head><body>"
                        + b"x" * 20000
                        + b"</body></html>",
                headers={"content-type": "text/html"},
            )

        client, _ = client_factory(handler)
        try:
            client.myself()
            raise AssertionError("ожидался JiraAuthError")
        except JiraAuthError as exc:
            assert exc.is_json is False
            assert exc.reason == "invalid_cookie_or_expired"
            assert exc.needs_reauthorization is True
            # Пользователю показывается короткая причина, а не страница.
            assert "<html>" not in exc.summary
            assert len(exc.summary) < 250
            # Исходный текст сохраняется для дампа пробы.
            assert "<html>" in exc.body_head

    def test_401_with_permission_text_becomes_permission_error(self, client_factory):
        client, _ = client_factory(_ok(NO_PERMISSION_BODY, status=401))
        from jira_client.errors import JiraPermissionError

        try:
            client.get_issue("ANALYTICS-0001")
            raise AssertionError("ожидался JiraPermissionError")
        except JiraPermissionError:
            pass

    def test_last_call_is_recorded(self, client_factory):
        client, _ = client_factory(_ok(AUTH_OK_BODY))
        client.myself()
        assert client.last_call["status"] == 200
        assert client.last_call["outcome"] == "ok"
        assert client.last_call["path"] == "/rest/api/2/myself"

    def test_last_call_has_no_body_or_headers(self, client_factory):
        client, _ = client_factory(_ok(AUTH_OK_BODY))
        client.myself()
        blob = json.dumps(client.last_call, ensure_ascii=False)
        assert "headers" not in blob
        assert FAKE_TOKEN not in blob


class TestNoRetryByDefault:
    def test_failed_call_is_not_retried(self, client_factory):
        """NFR-009: повтор внутри 10-секундного лимита означал бы его нарушение."""
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            return json_response(NO_AUTH_BODY, status=401)

        client, calls = client_factory(handler)
        try:
            client.myself()
        except JiraAuthError:
            pass
        assert attempts["n"] == 1
        assert len(calls) == 1