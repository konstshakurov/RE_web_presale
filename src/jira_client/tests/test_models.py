# -*- coding: utf-8 -*-
"""Разбор ответов Jira в модели клиента."""

from __future__ import annotations

from jira_client.models import (
    Attachment,
    Comment,
    Demand,
    FieldSpec,
    Person,
    Reference,
    SearchPage,
    ServerInfo,
    Transition,
)


class TestPerson:
    def test_display_name_is_preferred(self):
        person = Person.from_json(
            {"name": "testuser", "displayName": "Тестовый Пользователь"}
        )
        assert person.display_name == "Тестовый Пользователь"
        assert person.account_id == "testuser"

    def test_email_is_never_carried(self):
        """Правило пробы: e-mail не переносится в код и отчёты."""
        person = Person.from_json(
            {"displayName": "Тестовый", "emailAddress": "test@corp.example.internal"}
        )
        assert "email" not in repr(person).lower()

    def test_plain_string(self):
        assert Person.from_json("testuser").display_name == "testuser"

    def test_none_and_garbage(self):
        assert Person.from_json(None) is None
        assert Person.from_json(42) is None
        assert Person.from_json({}) is None

    def test_str_falls_back(self):
        assert str(Person(display_name=None)) == "неизвестно"


class TestReference:
    def test_status_with_category(self):
        ref = Reference.from_json(
            {"id": "3", "name": "В процессе", "statusCategory": {"key": "indeterminate"}}
        )
        assert ref.name == "В процессе"
        assert ref.category_key == "indeterminate"

    def test_without_category(self):
        ref = Reference.from_json({"id": "10010", "name": "Demand"})
        assert ref.category_key is None

    def test_empty_name_returns_none(self):
        assert Reference.from_json({"id": "1", "name": "  "}) is None

    def test_none(self):
        assert Reference.from_json(None) is None


class TestTransition:
    def _raw(self) -> dict:
        return {
            "id": "31",
            "name": "Кандидатов нет",
            "to": {"id": "7", "name": "Кандидатов нет"},
            "hasScreen": False,
            "fields": [
                {
                    "fieldId": "customfield_20904",
                    "name": "Причина отсутствия кандидата",
                    "required": True,
                    "schema": {"type": "option"},
                    "allowedValues": [
                        {"value": "opt1", "id": "1", "name": "требуется очное присутствие"},
                        {"value": "opt2", "id": "2", "name": "другая причина"},
                    ],
                },
                {
                    "fieldId": "summary",
                    "name": "Сводка",
                    "required": False,
                    "schema": {"type": "string"},
                },
            ],
        }

    def test_parses_id_name_and_target(self):
        transition = Transition.from_json(self._raw())
        assert transition.id == "31"
        assert transition.name == "Кандидатов нет"
        assert transition.to_status.name == "Кандидатов нет"

    def test_required_fields_are_separated(self):
        transition = Transition.from_json(self._raw())
        assert [f.field_id for f in transition.required_fields] == ["customfield_20904"]

    def test_optional_field_is_not_required(self):
        transition = Transition.from_json(self._raw())
        assert "summary" in [f.field_id for f in transition.fields]
        assert "summary" not in [f.field_id for f in transition.required_fields]

    def test_allowed_values_are_parsed(self):
        transition = Transition.from_json(self._raw())
        spec = transition.required_fields[0]
        assert [v.name for v in spec.allowed_values] == [
            "требуется очное присутствие",
            "другая причина",
        ]

    def test_value_hint_lists_allowed_values(self):
        transition = Transition.from_json(self._raw())
        assert "требуется очное присутствие" in transition.required_fields[0].value_hint()

    def test_describe_includes_required_fields(self):
        text = Transition.from_json(self._raw()).describe()
        assert "31" in text and "Кандидатов нет" in text
        assert "Причина отсутствия кандидата" in text

    def test_describe_without_required_fields(self):
        raw = {"id": "11", "name": "Переоткрыть", "to": {"name": "Открыт"}, "fields": []}
        text = Transition.from_json(raw).describe()
        assert "11" in text and "обязательно" not in text

    def _raw_dict_fields(self) -> dict:
        """Форма, которую Jira 8.13.0 отдаёт на живом экземпляре.

        Установлено пробой 2026-10-05, переход id 171 «Кандидатов нет»:
        ``transitions[].fields`` — словарь «fieldId → описание», а не список.
        """
        return {
            "id": "171",
            "name": "Кандидатов нет",
            "to": {"id": "11505", "name": "Кандидатов нет"},
            "hasScreen": False,
            "fields": {
                "customfield_16100": {
                    "required": False,
                    "name": "Причина отсутствия кандидата",
                    "schema": {"type": "option", "customId": 16100},
                    "fieldId": "customfield_16100",
                    "operations": ["set"],
                    "allowedValues": [
                        {"value": "Требуется очное присутствие", "id": "23300"},
                    ],
                },
                "customfield_20810": {
                    "required": True,
                    "name": "Включает НДС",
                    "schema": {"type": "option", "customId": 20810},
                    "fieldId": "customfield_20810",
                    "allowedValues": [{"value": "Да", "id": "20999"}],
                },
            },
        }

    def test_dict_shaped_fields_are_parsed(self):
        transition = Transition.from_json(self._raw_dict_fields())
        assert [f.field_id for f in transition.fields] == [
            "customfield_16100",
            "customfield_20810",
        ]

    def test_dict_shaped_required_field_is_not_lost(self):
        # Регресс: при обходе словаря FieldSpec.from_json получал строку-ключ,
        # возвращал None, и обязательные поля исчезали молча.
        transition = Transition.from_json(self._raw_dict_fields())
        assert [f.field_id for f in transition.required_fields] == ["customfield_20810"]

    def test_dict_shaped_allowed_values_survive(self):
        transition = Transition.from_json(self._raw_dict_fields())
        spec = transition.fields[0]
        assert spec.allowed_values is not None
        assert "Требуется очное присутствие" in spec.value_hint()

    def test_dict_shaped_transition_describes_required(self):
        assert "Включает НДС" in Transition.from_json(self._raw_dict_fields()).describe()

    def test_dict_key_used_when_fieldid_absent(self):
        raw = self._raw_dict_fields()
        del raw["fields"]["customfield_20810"]["fieldId"]
        transition = Transition.from_json(raw)
        assert [f.field_id for f in transition.required_fields] == ["customfield_20810"]

    def test_dict_with_unusable_values_is_skipped(self):
        raw = self._raw_dict_fields()
        raw["fields"]["summary"] = "не объект"
        raw["fields"]["customfield_20810"] = None
        transition = Transition.from_json(raw)
        assert [f.field_id for f in transition.fields] == ["customfield_16100"]
        assert transition.required_fields == []

    def test_fields_may_be_absent_or_none(self):
        for raw in ({"id": "1", "name": "n"}, {"id": "1", "name": "n", "fields": None}):
            transition = Transition.from_json(raw)
            assert transition.fields == []

    def test_transition_without_fields_key(self):
        transition = Transition.from_json({"id": "5", "name": "Далее", "to": {"name": "X"}})
        assert transition.fields == []
        assert transition.required_fields == []

    def test_malformed(self):
        assert Transition.from_json({"name": "без id"}) is None
        assert Transition.from_json("строка") is None


class TestFieldSpec:
    def test_missing_field_id_is_skipped(self):
        assert FieldSpec.from_json({"name": "без id"}) is None

    def test_schema_type_is_extracted(self):
        spec = FieldSpec.from_json(
            {"fieldId": "summary", "name": "Сводка", "required": True,
             "schema": {"type": "string"}}
        )
        assert spec.schema_type == "string"
        assert spec.required is True

    def test_allowed_values_absent_stays_none(self):
        spec = FieldSpec.from_json({"fieldId": "summary", "name": "Сводка"})
        assert spec.allowed_values is None
        assert spec.value_hint() is None


class TestDemand:
    def test_maps_core_fields(self, sample_issue):
        demand = Demand.from_json(sample_issue)
        assert demand.key == "ANALYTICS-0001"
        assert demand.summary == "Тестовая потребность"
        assert demand.status.name == "Тестовый статус"
        assert demand.status.category_key == "new"

    def test_extracts_epic_link(self, sample_issue):
        assert Demand.from_json(sample_issue).epic_key == "SA-0001"

    def test_collects_filled_rate_fields(self, sample_issue):
        rate = Demand.from_json(sample_issue).rate
        assert rate == {
            "customfield_20809": "1000",
            "customfield_20810": "2000",
        }

    def test_missing_fields_do_not_break_parsing(self):
        demand = Demand.from_json({"key": "ANALYTICS-0002", "fields": {}})
        assert demand.summary is None
        assert demand.status is None
        assert demand.rate == {}

    def test_absent_fields_container(self):
        assert Demand.from_json({"key": "X-1"}).key == "X-1"

    def test_issue_without_key_is_rejected(self):
        assert Demand.from_json({"fields": {}}) is None

    def test_priority_and_parent_absent_are_safe(self):
        """Jira их не отдаёт — код обязан это переживать."""
        demand = Demand.from_json({"key": "X-1", "fields": {"summary": "s"}})
        assert demand.key == "X-1"


class TestComment:
    def test_parses_body_and_author(self):
        comment = Comment.from_json(
            {
                "id": "10000",
                "author": {"displayName": "Тестовый Пользователь"},
                "body": "Текст",
                "created": "2026-10-04T12:00:00.000+0300",
                "updated": "2026-10-04T12:01:00.000+0300",
            }
        )
        assert comment.id == "10000"
        assert comment.body == "Текст"
        assert comment.author.display_name == "Тестовый Пользователь"

    def test_missing_body_becomes_empty(self):
        assert Comment.from_json({"id": "1"}).body == ""

    def test_without_id_is_none(self):
        assert Comment.from_json({"body": "Текст"}) is None


class TestAttachment:
    def test_parses_size_and_name(self):
        attachment = Attachment.from_json(
            {
                "id": "5000",
                "filename": "резюме.pdf",
                "size": 12345,
                "mimeType": "application/pdf",
                "created": "2026-10-01T10:00:00.000+0300",
                "content": "https://jira.example.internal/secure/attachment/5000/x.pdf",
            }
        )
        assert attachment.filename == "резюме.pdf"
        assert attachment.size == 12345
        assert attachment.mime_type == "application/pdf"

    def test_missing_size_becomes_zero(self):
        assert Attachment.from_json({"id": "1", "filename": "a.txt"}).size == 0

    def test_without_filename_is_none(self):
        assert Attachment.from_json({"id": "1"}) is None


class TestSearchPage:
    def test_pagination_arithmetic(self):
        page = SearchPage(issues=[{"key": "A-1"}], total=282, start_at=0, max_results=100)
        assert page.next_start_at == 1
        assert page.is_last is False

    def test_last_page(self):
        page = SearchPage(issues=[{"key": "A-3"}, {"key": "A-4"}], total=282, start_at=280,
                          max_results=100)
        assert page.next_start_at == 282
        assert page.is_last is True

    def test_unknown_total_is_last(self):
        assert SearchPage(issues=[], total=None, start_at=0, max_results=100).is_last is True


class TestServerInfo:
    def test_parses(self):
        info = ServerInfo.from_json(
            {"baseUrl": "https://jira.example.internal", "version": "8.13.0",
             "deploymentType": "Server"}
        )
        assert info.version == "8.13.0"
        assert info.deployment_type == "Server"

    def test_empty(self):
        info = ServerInfo.from_json(None)
        assert info.version is None