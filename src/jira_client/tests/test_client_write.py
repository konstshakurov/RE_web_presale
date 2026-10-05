# -*- coding: utf-8 -*-
"""Запись в Jira: комментарий, вложение, переход статуса, изменение полей."""

from __future__ import annotations

import httpx
import pytest

from jira_client.errors import JiraBadRequestError
from tests.conftest import NO_AUTH_BODY, json_response


def _status(code: int, payload=None):
    return lambda request: json_response(
        payload if payload is not None else {}, status=code
    )


class TestAddComment:
    def test_posts_body(self, client_factory):
        client, calls = client_factory(
            _status(201, {"id": "10100", "body": "Текст комментария",
                          "author": {"displayName": "Тестовый Пользователь"},
                          "created": "2026-10-05T10:00:00.000+0300"})
        )
        comment = client.add_comment("ANALYTICS-0001", "Текст комментария")
        assert comment.id == "10100"
        assert calls[0].method == "POST"
        assert calls[0].path == "/rest/api/2/issue/ANALYTICS-0001/comment"
        assert calls[0].json == {"body": "Текст комментария"}

    def test_uses_cookie_auth(self, client_factory):
        """Запись идёт от имени пользователя: в заголовке сессионная cookie."""
        from tests.conftest import FAKE_TOKEN

        client, calls = client_factory(_status(201, {"id": "1", "body": "x"}))
        client.add_comment("ANALYTICS-0001", "x")
        assert calls[0].header("Cookie") == "JSESSIONID=" + FAKE_TOKEN

    def test_empty_body_is_rejected_locally(self, client_factory):
        client, calls = client_factory(_status(201, {"id": "1"}))
        with pytest.raises(ValueError):
            client.add_comment("ANALYTICS-0001", "   ")
        assert calls == [], "пустой комментарий не должен уходить в Jira"

    def test_201_with_empty_body_is_tolerated(self, client_factory):
        client, _ = client_factory(_status(201, None))
        comment = client.add_comment("ANALYTICS-0001", "Текст")
        assert comment.body == "Текст"
        assert comment.id == ""


class TestDeleteComment:
    def test_deletes(self, client_factory):
        client, calls = client_factory(_status(204, None))
        assert client.delete_comment("ANALYTICS-0001", "10100") is True
        assert calls[0].method == "DELETE"
        assert calls[0].path == "/rest/api/2/issue/ANALYTICS-0001/comment/10100"

    def test_404_on_missing_comment(self, client_factory):
        client, _ = client_factory(
            _status(404, {"errorMessages": ["ЗАПРОС НЕ СУЩЕСТВУЕТ"], "errors": {}})
        )
        from jira_client.errors import JiraNotFoundError

        with pytest.raises(JiraNotFoundError):
            client.delete_comment("ANALYTICS-0001", "999")


class TestAddAttachment:
    def test_multipart_upload_with_token_header(self, client_factory):
        client, calls = client_factory(
            _status(200, [{"id": "5001", "filename": "cv.pdf", "size": 4,
                           "mimeType": "application/pdf"}])
        )
        attachments = client.add_attachment(
            "ANALYTICS-0001", filename="cv.pdf", content=b"%PDF", mime_type="application/pdf"
        )
        assert attachments[0].filename == "cv.pdf"
        assert calls[0].method == "POST"
        assert calls[0].path == "/rest/api/2/issue/ANALYTICS-0001/attachments"
        # Jira Server требует этот заголовок, иначе загрузка отклоняется.
        assert calls[0].header("X-Atlassian-Token") == "no-check"
        assert calls[0].header("content-type").startswith("multipart/form-data")

    def test_file_reaches_the_request_body(self, client_factory):
        client, calls = client_factory(_status(200, []))
        client.add_attachment("ANALYTICS-0001", filename="cv.pdf", content=b"%PDF-1.4")
        raw = calls[0].json
        assert raw is None, "multipart отправляется телом, а не json"
        body = calls[0].raw_content
        assert b'filename="cv.pdf"' in body
        assert b"%PDF-1.4" in body
        assert b'name="file"' in body

    def test_size_limit_is_enforced_locally(self, client_factory):
        client, calls = client_factory(_status(200, []))
        with pytest.raises(ValueError) as exc:
            client.add_attachment(
                "ANALYTICS-0001", filename="big.bin", content=b"x" * 100, size_limit=10
            )
        assert "превышает лимит" in str(exc.value)
        assert calls == []

    def test_empty_file_is_rejected(self, client_factory):
        client, _ = client_factory(_status(200, []))
        with pytest.raises(ValueError):
            client.add_attachment("ANALYTICS-0001", filename="a.txt", content=b"")

    def test_empty_filename_is_rejected(self, client_factory):
        client, _ = client_factory(_status(200, []))
        with pytest.raises(ValueError):
            client.add_attachment("ANALYTICS-0001", filename="", content=b"x")

    def test_non_json_response_yields_empty_list(self, client_factory):
        def handler(request):
            return httpx.Response(200, content=b"ok", headers={"content-type": "text/plain"})

        client, _ = client_factory(handler)
        assert client.add_attachment("ANALYTICS-0001", filename="a.txt", content=b"x") == []


class TestDeleteAttachment:
    def test_deletes(self, client_factory):
        client, calls = client_factory(_status(204, None))
        assert client.delete_attachment("ANALYTICS-0001", "5001") is True
        assert calls[0].method == "DELETE"
        assert calls[0].path == "/rest/api/2/issue/ANALYTICS-0001/attachment/5001"


class TestTransition:
    def _transition_payload(self):
        return {
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
                }
            ]
        }

    def _handler(self, transition_status=204):
        def handler(request):
            if request.method == "GET":
                return json_response(self._transition_payload())
            return json_response({}, status=transition_status)

        return handler

    def test_preflight_then_post(self, client_factory):
        """Клиент перечитывает переходы перед записью — FR-028."""
        client, calls = client_factory(self._handler())
        client.transition(
            "ANALYTICS-0001",
            "31",
            fields={"customfield_20904": {"value": "требуется очное присутствие"}},
        )
        assert len(calls) == 2
        assert calls[0].method == "GET"
        assert calls[0].path.endswith("/transitions")
        assert calls[1].method == "POST"
        assert calls[1].path.endswith("/transitions")

    def test_post_body_shape(self, client_factory):
        client, calls = client_factory(self._handler())
        client.transition(
            "ANALYTICS-0001",
            "31",
            fields={"customfield_20904": {"value": "требуется очное присутствие"}},
        )
        body = calls[1].json
        assert body["transition"] == {"id": "31"}
        assert body["fields"] == {
            "customfield_20904": {"value": "требуется очное присутствие"}
        }

    def test_missing_required_field_is_blocked_locally(self, client_factory):
        """Лучше не отправлять, чем получить 400 с невнятным текстом."""
        client, calls = client_factory(self._handler())
        with pytest.raises(JiraBadRequestError) as exc:
            client.transition("ANALYTICS-0001", "31")
        assert "Причина отсутствия кандидата" in str(exc.value)
        assert len(calls) == 1, "POST не должен уходить"
        assert calls[0].method == "GET"

    def test_unknown_transition_is_blocked_locally(self, client_factory):
        client, calls = client_factory(self._handler())
        with pytest.raises(JiraBadRequestError) as exc:
            client.transition("ANALYTICS-0001", "999")
        assert "недоступен" in str(exc.value)
        assert len(calls) == 1

    def test_validation_can_be_skipped(self, client_factory):
        client, calls = client_factory(self._handler())
        client.transition("ANALYTICS-0001", "31", validate=False)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_update_block_is_forwarded(self, client_factory):
        client, calls = client_factory(self._handler())
        client.transition(
            "ANALYTICS-0001", "31",
            fields={"customfield_20904": {"value": "x"}},
            update={"comment": [{"add": {"body": "Комментарий"}}]},
        )
        assert calls[1].json["update"]["comment"][0]["add"]["body"] == "Комментарий"

    def test_comment_is_sent_through_update_add(self, client_factory):
        """Переход 171 требует комментарий; принимается только update.comment.add."""
        client, calls = client_factory(self._handler())
        client.transition(
            "ANALYTICS-0001", "171",
            fields={"customfield_16100": {"id": "23300"}},
            comment="Причина: требуется очное присутствие",
            validate=False,
        )
        body = calls[0].json
        assert body["update"]["comment"][0]["add"]["body"] == \
            "Причина: требуется очное присутствие"
        # Комментарий не должен попадать в fields: Jira отвечает на это
        # 400 «Field does not support update 'comment'».
        assert "comment" not in body.get("fields", {})

    def test_comment_and_explicit_update_are_merged(self, client_factory):
        client, calls = client_factory(self._handler())
        client.transition(
            "ANALYTICS-0001", "31",
            update={"labels": [{"add": "проба"}]},
            comment="Текст",
            validate=False,
        )
        update = calls[0].json["update"]
        assert update["labels"] == [{"add": "проба"}]
        assert update["comment"][0]["add"]["body"] == "Текст"

    def test_explicit_comment_operation_is_not_duplicated(self, client_factory):
        client, calls = client_factory(self._handler())
        client.transition(
            "ANALYTICS-0001", "31",
            update={"comment": [{"add": {"body": "Уже есть"}}]},
            comment="Ещё раз",
            validate=False,
        )
        assert len(calls[0].json["update"]["comment"]) == 2

    def test_no_comment_means_no_update_block(self, client_factory):
        client, calls = client_factory(self._handler())
        client.transition("ANALYTICS-0001", "31", validate=False)
        assert "update" not in calls[0].json

    def test_empty_comment_means_no_update_block(self, client_factory):
        client, calls = client_factory(self._handler())
        client.transition("ANALYTICS-0001", "31", comment="", validate=False)
        assert "update" not in calls[0].json

    def test_jira_400_is_surfaced_verbatim(self, client_factory):
        """FR-029: причина отказа показывается как есть."""
        def handler(request):
            if request.method == "GET":
                return json_response(self._transition_payload())
            return json_response(
                {"errorMessages": ["Не указано обязательное поле"],
                 "errors": {"customfield_20904": "Укажите значение"}},
                status=400,
            )

        client, _ = client_factory(handler)
        # Отключаем локальную валидацию, чтобы дойти до ответа Jira.
        with pytest.raises(JiraBadRequestError) as exc:
            client.transition("ANALYTICS-0001", "31", validate=False)
        assert exc.value.status == 400
        assert exc.value.field_errors == {"customfield_20904": "Укажите значение"}

    def test_401_on_write(self, client_factory):
        client, _ = client_factory(
            lambda request: json_response(NO_AUTH_BODY, status=401)
        )
        from jira_client.errors import JiraAuthError

        with pytest.raises(JiraAuthError):
            client.transition("ANALYTICS-0001", "31", validate=False)

    def test_update_without_fields_is_omitted(self, client_factory):
        client, calls = client_factory(self._handler())
        client.transition("ANALYTICS-0001", "31", validate=False)
        assert "fields" not in calls[0].json
        assert "update" not in calls[0].json


class TestSetFields:
    def test_puts_fields(self, client_factory):
        client, calls = client_factory(_status(204, None))
        assert client.set_fields("ANALYTICS-0001", {"customfield_20809": "1500"}) is True
        assert calls[0].method == "PUT"
        assert calls[0].path == "/rest/api/2/issue/ANALYTICS-0001"
        assert calls[0].json == {"fields": {"customfield_20809": "1500"}}

    def test_empty_fields_rejected_locally(self, client_factory):
        client, calls = client_factory(_status(204, None))
        with pytest.raises(ValueError):
            client.set_fields("ANALYTICS-0001", {})
        assert calls == []


class TestAssign:
    def test_assigns_by_username(self, client_factory):
        client, calls = client_factory(_status(204, None))
        assert client.assign("ANALYTICS-0001", "testuser") is True
        assert calls[0].json == {"fields": {"assignee": {"name": "testuser"}}}


class TestRateUpdateFlow:
    """Сценарий FR-018/FR-008: ставка плюс назначение и комментарий."""

    def test_full_write_sequence(self, client_factory):
        def handler(request):
            if request.method == "PUT":
                return json_response({}, status=204)
            if request.method == "POST" and request.url.path.endswith("/comment"):
                return json_response({"id": "1", "body": "Комментарий"}, status=201)
            return json_response({"issues": [], "total": 0})

        client, calls = client_factory(handler)
        client.set_fields("ANALYTICS-0001", {"customfield_20811": "1800"})
        client.assign("ANALYTICS-0001", "testuser")
        client.add_comment("ANALYTICS-0001", "Назначен тестовый кандидат")

        assert [c.method for c in calls] == ["PUT", "PUT", "POST"]
        assert calls[-1].json == {"body": "Назначен тестовый кандидат"}