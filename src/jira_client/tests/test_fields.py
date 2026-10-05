# -*- coding: utf-8 -*-
"""Идентификаторы полей и правила отбора заполненных полей (FR-015, FR-028)."""

from __future__ import annotations

from jira_client import fields as F


class TestFieldIds:
    def test_epic_link_matches_fr_028(self):
        """FR-028 явно фиксирует Epic Link = customfield_10101."""
        assert F.EPIC_LINK == "customfield_10101"

    def test_rate_fields_are_four_consecutive(self):
        assert F.RATE_FIELDS == (
            "customfield_20809",
            "customfield_20810",
            "customfield_20811",
            "customfield_20812",
        )

    def test_epic_rules_match_fr_015_list(self):
        """FR-015 перечисляет 13 полей эпика клиента."""
        assert set(F.EPIC_RULES) == {
            "customfield_12000",  # Рекомендации
            "customfield_21801",  # Условия для производства
            "customfield_20909",  # Критерии приемки
            "customfield_20910",  # Штрафы
            "customfield_20907",  # Привлечение третьих лиц
            "customfield_20913",  # Разрешение на PR
            "customfield_20903",  # Постановка задач
            "customfield_20904",  # РИД
            "customfield_20905",  # Обязанности Заказчика
            "customfield_20906",  # Автоприемка
            "customfield_14024",  # Формат работы
            "customfield_14023",  # Тип договора
            "customfield_14205",  # NDA
        }

    def test_demand_rules_match_fr_015_list(self):
        assert set(F.DEMAND_RULES) == {
            "customfield_20302",  # Рисовать опыт нельзя
            "customfield_20914",  # Работа вне РФ
            "customfield_11506",  # Нужно предоставить CV
            "customfield_10811",  # Требования к кандидату
        }

    def test_rule_fields_combine_both_groups(self):
        assert len(F.RULE_FIELDS) == len(F.EPIC_RULES) + len(F.DEMAND_RULES) == 17

    def test_no_duplicate_ids(self):
        everything = list(F.EPIC_RULES) + list(F.DEMAND_RULES) + list(F.RATE_FIELDS)
        assert len(everything) == len(set(everything))


class TestResolveFieldId:
    def test_id_passes_through(self):
        assert F.resolve_field_id("customfield_10101") == "customfield_10101"

    def test_display_name_resolves(self):
        assert F.resolve_field_id("Epic Link") == "customfield_10101"

    def test_russian_name_resolves(self):
        assert F.resolve_field_id("NDA") == "customfield_14205"
        assert F.resolve_field_id("Штрафы") == "customfield_20910"

    def test_case_insensitive(self):
        assert F.resolve_field_id("epic link") == "customfield_10101"

    def test_unknown_returns_none(self):
        """Лучше None, чем неверный ID в JQL: полей на экземпляре 833."""
        assert F.resolve_field_id("Поле которого нет") is None
        assert F.resolve_field_id("") is None

    def test_surrounding_whitespace_is_ignored(self):
        assert F.resolve_field_id("  Epic Link  ") == "customfield_10101"


class TestHumanize:
    def test_known_field(self):
        assert F.humanize("customfield_10101") == "Epic Link"

    def test_unknown_field_returns_id(self):
        assert F.humanize("customfield_99999") == "customfield_99999"


class TestBuildFieldIndex:
    def test_indexes_by_id(self):
        raw = [
            {"id": "summary", "name": "Сводка", "custom": False},
            {"id": "customfield_10101", "name": "Epic Link", "custom": True},
        ]
        index = F.build_field_index(raw)
        assert index["customfield_10101"]["name"] == "Epic Link"
        assert index["summary"]["custom"] is False

    def test_skips_malformed_entries(self):
        index = F.build_field_index([{"no": "id"}, "строка", None, 42, {"id": "ok"}])
        assert set(index) == {"ok"}

    def test_empty_input(self):
        assert F.build_field_index([]) == {}
        assert F.build_field_index(None) == {}


class TestPickFilled:
    """FR-015: показывать только заполненные поля."""

    def test_skips_none_empty_string_and_empty_containers(self):
        fields = {
            "customfield_12000": "Рекомендации",
            "customfield_20909": None,
            "customfield_20910": "   ",
            "customfield_20907": [],
            "customfield_20913": {},
            "customfield_20903": False,
            "customfield_20904": 0,
        }
        picked = F.pick_filled(fields, F.EPIC_RULES)
        ids = [item[0] for item in picked]
        assert "customfield_12000" in ids
        assert "customfield_20903" in ids  # False — это заполненное значение
        assert "customfield_20904" in ids  # 0 — тоже заполненное
        assert "customfield_20909" not in ids
        assert "customfield_20910" not in ids
        assert "customfield_20907" not in ids
        assert "customfield_20913" not in ids

    def test_skips_absent_keys(self):
        picked = F.pick_filled({"customfield_12000": "да"}, F.EPIC_RULES)
        assert len(picked) == 1

    def test_returns_id_value_and_name(self):
        picked = F.pick_filled({"customfield_14205": "да"}, F.EPIC_RULES)
        field_id, value, name = picked[0]
        assert field_id == "customfield_14205"
        assert value == "да"
        assert name == "NDA"


class TestFieldNameFromMeta:
    def test_prefers_name(self):
        assert F.field_name_from_meta({"name": "Epic Link", "id": "x"}) == "Epic Link"

    def test_falls_back_to_id(self):
        assert F.field_name_from_meta({"id": "customfield_1"}) == "customfield_1"

    def test_blank_name_falls_back(self):
        assert F.field_name_from_meta({"name": "   ", "id": "y"}) == "y"


class TestReadFieldSets:
    def test_demand_read_fields_include_epic_and_rates(self):
        assert F.EPIC_LINK in F.DEMAND_READ_FIELDS
        for rate_id in F.RATE_FIELDS:
            assert rate_id in F.DEMAND_READ_FIELDS

    def test_demand_read_fields_exclude_epic_rules(self):
        """Правила эпика не грузятся вместе с каждой потребностью."""
        assert not set(F.EPIC_RULES) & set(F.DEMAND_READ_FIELDS)

    def test_epic_read_fields_include_all_epic_rules(self):
        assert set(F.EPIC_RULES) <= set(F.EPIC_READ_FIELDS)

    def test_default_search_fields_are_narrow(self):
        """FR-028: запрашивать только нужные поля."""
        assert len(F.DEFAULT_SEARCH_FIELDS) < 10
        assert "summary" in F.DEFAULT_SEARCH_FIELDS
        assert "updated" in F.DEFAULT_SEARCH_FIELDS