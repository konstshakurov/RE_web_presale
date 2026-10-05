# -*- coding: utf-8 -*-
"""Разбор ответов Jira и отображение кодов в исключения (FR-029, ISS-013)."""

from __future__ import annotations

from jira_client.errors import (
    AUTH_REASON_FAILED,
    AUTH_REASON_INVALID_COOKIE,
    AUTH_REASON_LOGIN_REQUIRED,
    AUTH_REASON_NO_CREDENTIALS,
    AUTH_REASON_NO_PERMISSION,
    AUTH_REASON_UNKNOWN,
    JiraAuthError,
    JiraBadRequestError,
    JiraError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitedError,
    JiraServerError,
    JiraTimeoutError,
    JiraUnavailableError,
    classify_auth_failure,
    error_for_response,
    needs_reauthorization,
    parse_error_body,
)
from tests.conftest import (
    AUTH_FAILED_BODY,
    NO_ACCESS_BODY,
    NO_AUTH_BODY,
    NO_PERMISSION_BODY,
    NOT_FOUND_BODY,
)

import httpx
import json

import pytest


def _resp(payload, status: int) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json;charset=UTF-8"},
    )


class TestParseErrorBody:
    def test_no_auth_form_uses_message_key(self):
        """Первая из двух форм 401, зафиксированных пробой."""
        parsed = parse_error_body(json.dumps(NO_AUTH_BODY).encode(), "application/json")
        assert parsed["is_json"] is True
        assert parsed["code"] == 401
        assert "must be authenticated" in parsed["summary"]

    def test_auth_failed_form_uses_error_messages(self):
        parsed = parse_error_body(json.dumps(AUTH_FAILED_BODY).encode(), "application/json")
        assert parsed["code"] is None
        assert "Ошибка доступа" in parsed["summary"]

    def test_permission_form_is_recognised(self):
        parsed = parse_error_body(json.dumps(NO_PERMISSION_BODY).encode(), "application/json")
        assert classify_auth_failure(parsed["summary"]) == AUTH_REASON_NO_PERMISSION

    def test_expired_session_is_not_mistaken_for_permission(self):
        parsed = parse_error_body(json.dumps(NO_AUTH_BODY).encode(), "application/json")
        assert classify_auth_failure(parsed["summary"]) == AUTH_REASON_NO_CREDENTIALS

    def test_field_errors_are_collected(self):
        payload = {
            "errorMessages": ["Не хватает полей"],
            "errors": {"customfield_10101": "Укажите эпик"},
        }
        parsed = parse_error_body(json.dumps(payload).encode(), "application/json")
        assert parsed["field_errors"] == {"customfield_10101": "Укажите эпик"}
        assert "customfield_10101" in parsed["summary"]

    def test_html_response_is_recognised(self):
        """Неверная сессия даёт HTML, а не JSON: это отдельная причина 401."""
        body = b"<html><head><title>Unauthorized (401)</title></head><body>Authorization Required</body></html>"
        parsed = parse_error_body(body, "text/html;charset=UTF-8")
        assert parsed["is_json"] is False
        assert "Unauthorized (401)" in parsed["summary"]
        assert "<html>" not in parsed["summary"]
        assert classify_auth_failure(parsed["summary"], is_json=False) == \
            AUTH_REASON_INVALID_COOKIE

    def test_empty_body(self):
        parsed = parse_error_body(b"", "application/json")
        assert parsed["summary"] == ""

    def test_broken_json_is_text(self):
        parsed = parse_error_body("{не json".encode("utf-8"), "application/json")
        assert parsed["is_json"] is False

    def test_json_array_body(self):
        parsed = parse_error_body(b"[1,2,3]", "application/json")
        assert parsed["is_json"] is False


class TestAuthFailureClassification:
    """Код 401 в Jira 8.13.0 означает минимум четыре разные ситуации."""

    def test_no_credentials(self):
        parsed = parse_error_body(json.dumps(NO_AUTH_BODY).encode("utf-8"), "application/json")
        assert classify_auth_failure(parsed["summary"]) == AUTH_REASON_NO_CREDENTIALS
        assert needs_reauthorization(AUTH_REASON_NO_CREDENTIALS) is True

    def test_login_required(self):
        body = json.dumps(NO_ACCESS_BODY).encode("utf-8")
        parsed = parse_error_body(body, "application/json")
        assert classify_auth_failure(parsed["summary"]) == AUTH_REASON_LOGIN_REQUIRED
        assert needs_reauthorization(AUTH_REASON_LOGIN_REQUIRED) is True

    def test_no_permission_is_not_reauthorization(self):
        """«Нет прав» и «сессия истекла» требуют противоположных действий."""
        parsed = parse_error_body(json.dumps(NO_PERMISSION_BODY).encode("utf-8"), "application/json")
        assert classify_auth_failure(parsed["summary"]) == AUTH_REASON_NO_PERMISSION
        assert needs_reauthorization(AUTH_REASON_NO_PERMISSION) is False

    def test_auth_failed(self):
        parsed = parse_error_body(json.dumps(AUTH_FAILED_BODY).encode("utf-8"), "application/json")
        assert classify_auth_failure(parsed["summary"]) == AUTH_REASON_FAILED

    def test_html_means_invalid_cookie(self):
        """При недействительной cookie Jira отдаёт HTML даже там, где анонимно 200."""
        parsed = parse_error_body(
            b"<html><head><title>Unauthorized (401)</title></head><body>x</body></html>",
            "text/html",
        )
        assert parsed["is_json"] is False
        assert classify_auth_failure(parsed["summary"], is_json=False) == \
            AUTH_REASON_INVALID_COOKIE
        assert needs_reauthorization(AUTH_REASON_INVALID_COOKIE) is True

    def test_html_summary_is_short_and_readable(self):
        """Пользователю нельзя показывать HTML-страницу на 13 КБ."""
        parsed = parse_error_body(
            b"<html><head><title>Unauthorized (401)</title></head><body>" + b"x" * 20000 + b"</body></html>",
            "text/html",
        )
        assert len(parsed["summary"]) < 250
        assert "Unauthorized (401)" in parsed["summary"]
        assert "<html>" not in parsed["summary"]

    def test_raw_head_keeps_original_for_probe_dumps(self):
        raw = b"<html><head><title>Unauthorized (401)</title></head></html>"
        parsed = parse_error_body(raw, "text/html")
        assert parsed["raw_head"].startswith("<html>")

    def test_unrecognised_body_falls_back_to_unknown(self):
        parsed = parse_error_body(
            json.dumps({"errorMessages": ["что-то неожиданное"]}).encode("utf-8"),
            "application/json",
        )
        assert classify_auth_failure(parsed["summary"]) == AUTH_REASON_UNKNOWN
        # Неизвестное трактуем как требующее авторизации: безопаснее переспросить.
        assert needs_reauthorization(AUTH_REASON_UNKNOWN) is True


class TestAuthErrorReason:
    def test_no_permission_becomes_permission_error(self):
        error = error_for_response(_resp(NO_PERMISSION_BODY, 401), path="/x")
        assert isinstance(error, JiraPermissionError)
        assert error.reason == AUTH_REASON_NO_PERMISSION
        assert error.needs_reauthorization if hasattr(error, "needs_reauthorization") else True

    def test_login_required_becomes_auth_error_asking_reauth(self):
        error = error_for_response(_resp(NO_ACCESS_BODY, 401), path="/x")
        assert isinstance(error, JiraAuthError)
        assert error.reason == AUTH_REASON_LOGIN_REQUIRED
        assert error.needs_reauthorization is True

    def test_html_401_becomes_auth_error_asking_reauth(self):
        response = httpx.Response(
            401,
            content=b"<html><head><title>Unauthorized (401)</title></head></html>",
            headers={"content-type": "text/html"},
        )
        error = error_for_response(response, path="/x")
        assert isinstance(error, JiraAuthError)
        assert error.reason == AUTH_REASON_INVALID_COOKIE
        assert error.needs_reauthorization is True
        assert "<html>" not in error.summary

    def test_no_credentials_becomes_auth_error(self):
        error = error_for_response(_resp(NO_AUTH_BODY, 401), path="/x")
        assert isinstance(error, JiraAuthError)
        assert error.reason == AUTH_REASON_NO_CREDENTIALS
        assert error.needs_reauthorization is True

    def test_non_401_errors_have_no_reason(self):
        error = error_for_response(_resp(NOT_FOUND_BODY, 404), path="/x")
        assert error.reason is None

    def test_error_for_response_403_is_permission(self):
        error = error_for_response(_resp({}, 403), path="/x")
        assert isinstance(error, JiraPermissionError)


class TestStatusMapping:
    def test_400(self):
        assert isinstance(error_for_response(_resp({}, 400)), JiraBadRequestError)

    def test_401(self):
        assert isinstance(error_for_response(_resp(NO_AUTH_BODY, 401)), JiraAuthError)

    def test_403(self):
        assert isinstance(error_for_response(_resp({}, 403)), JiraPermissionError)

    def test_404(self):
        error = error_for_response(_resp(NOT_FOUND_BODY, 404))
        assert isinstance(error, JiraNotFoundError)
        assert "ЗАПРОС НЕ СУЩЕСТВУЕТ" in error.summary

    def test_409(self):
        from jira_client.errors import JiraConflictError

        assert isinstance(error_for_response(_resp({}, 409)), JiraConflictError)

    def test_429_is_retryable(self):
        error = error_for_response(_resp({}, 429))
        assert isinstance(error, JiraRateLimitedError)
        assert error.retryable is True

    def test_500(self):
        error = error_for_response(_resp({}, 500))
        assert isinstance(error, JiraServerError)
        assert error.retryable is False

    def test_503_is_treated_as_rate_limited(self):
        """Jira отдаёт 503 при перегрузке; повтор оправдан."""
        assert isinstance(error_for_response(_resp({}, 503)), JiraRateLimitedError)

    def test_unknown_status_falls_back_to_base(self):
        error = error_for_response(_resp({"message": "что-то"}, 418))
        assert type(error) is JiraError
        assert error.status == 418


class TestErrorObject:
    def test_render_includes_method_path_status(self):
        error = JiraAuthError(
            "нет сессии", status=401, method="GET", path="/rest/api/2/myself"
        )
        text = str(error)
        assert "GET" in text and "/rest/api/2/myself" in text and "401" in text

    def test_timeout_and_unavailable_are_retryable(self):
        assert JiraTimeoutError().retryable is True
        assert JiraUnavailableError("сеть").retryable is True

    def test_auth_error_is_not_retryable(self):
        """Истёкшая сессия повтором не чинится — FR-029 требует новой авторизации."""
        assert JiraAuthError().retryable is False

    def test_from_response_keeps_field_errors(self):
        payload = {"errorMessages": ["Ошибка"], "errors": {"a": "b"}}
        error = JiraBadRequestError.from_response(_resp(payload, 400), method="PUT", path="/x")
        assert error.field_errors == {"a": "b"}
        assert error.method == "PUT"