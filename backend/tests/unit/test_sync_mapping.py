"""Сопоставление ответов Jira со строками кэша (KON-35): ставка, статус, эпик, сотрудник."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.sync.mapping import (
    NEED_FIELDS,
    jql_time,
    map_employee,
    map_epic_rules,
    map_need,
    parse_jira_dt,
)

from ._sync_fakes import issue

RATE = {
    "customfield_20809": 3050.0, "customfield_20810": {"value": "Нет"},
    "customfield_20811": {"value": "руб."}, "customfield_20812": {"value": "ч (час)"},
}


def test_dates_with_offset_are_parsed():
    dt = parse_jira_dt("2026-09-30T13:04:48.495+0400")
    assert dt.utcoffset().total_seconds() == 4 * 3600
    assert parse_jira_dt("мусор") is None and parse_jira_dt(None) is None


def test_jql_time_is_in_jira_timezone_and_floors_to_minute():
    moment = datetime(2026, 10, 9, 8, 30, 59, tzinfo=UTC)
    assert jql_time(moment, 4) == "2026-10-09 12:30"


def test_fields_list_is_explicit_and_excludes_parent_priority():
    assert "parent" not in NEED_FIELDS and "priority" not in NEED_FIELDS
    assert "customfield_10101" in NEED_FIELDS and len(NEED_FIELDS) < 20


def test_need_full_mapping():
    row = map_need(issue(
        "ANALYTICS-5853", "Кандидат назначен", description="Текст", reporter={"name": "autor"},
        assignee={"name": "ispolnitel"}, customfield_10101="SA-2219",
        customfield_10811="Python, FastAPI", customfield_19201={"value": "Прямой контракт"},
        customfield_21105={"value": "Клиент"}, **RATE))
    assert row.jira_key == "ANALYTICS-5853" and row.status == "Кандидат назначен"
    assert row.epic_key == "SA-2219" and row.assignee == "ispolnitel" and row.author == "autor"
    assert row.rate_amount == Decimal("3050.00") and row.rate_currency == "RUB"
    assert row.rate_includes_vat is False and row.rate_unit == "ч (час)"
    assert row.rate_proposer == "от клиента" and row.contract_type == "Прямой контракт"
    assert row.jira_updated_at.utcoffset().total_seconds() == 4 * 3600


def test_need_without_rate_and_epic_is_valid():
    row = map_need(issue("ANALYTICS-2"))
    assert row.rate_amount is None and row.epic_key is None and row.assignee is None
    assert not row.rate_dropped


def test_rub_without_vat_drops_rate_instead_of_violating_schema():
    f = dict(RATE)
    del f["customfield_20810"]
    row = map_need(issue("ANALYTICS-3", **f))
    assert row.rate_amount is None and row.rate_currency is None and row.rate_dropped


def test_unsupported_currency_drops_rate():
    f = dict(RATE, customfield_20811={"value": "EUR"})
    row = map_need(issue("ANALYTICS-4", **f))
    assert row.rate_amount is None and row.rate_dropped


def test_usd_without_vat_is_kept():
    f = dict(RATE, customfield_20811={"value": "USD"})
    del f["customfield_20810"]
    row = map_need(issue("ANALYTICS-5", **f))
    assert row.rate_amount == Decimal("3050.00") and row.rate_currency == "USD"
    assert row.rate_includes_vat is None


def test_unknown_contract_and_bad_records_are_skipped_safely():
    other = map_need(issue("ANALYTICS-6", customfield_19201={"value": "Другое"}))
    assert other.contract_type is None
    assert map_need({"key": "bad key", "fields": {}}) is None
    assert map_need({"key": "ANALYTICS-7", "fields": {"summary": "x"}}) is None
    assert map_need({"key": "ANALYTICS-8", "fields": None}) is None


def test_bad_epic_link_is_ignored():
    assert map_need(issue("ANALYTICS-9", customfield_10101="не ключ")).epic_key is None


def test_employee_assignable_by_status():
    ok = map_employee(issue("ANALYTICS-96", "Свободен", customfield_13504="Опыт"))
    assert ok.full_name == "Заявка ANALYTICS-96" and ok.assignable
    assert ok.extra_experience == "Опыт"
    for status in ("Коммерческий проект", "Недоступен для подключения", "Уволен", "Декрет"):
        assert map_employee(issue("ANALYTICS-97", status)).assignable is False
    assert map_employee({"key": "ANALYTICS-98", "fields": {"summary": " "}}) is None


def test_epic_rules_only_filled_fields():
    rules = map_epic_rules({"customfield_14205": "Есть NDA", "customfield_20910": "",
                            "customfield_12000": {"value": "Присылать CV по шаблону"},
                            "customfield_20904": None})
    assert [r["name"] for r in rules] == ["Рекомендации", "NDA"]
    assert rules[0]["value"] == "Присылать CV по шаблону"
