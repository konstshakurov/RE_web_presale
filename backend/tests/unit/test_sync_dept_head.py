"""Руководитель отдела (CR-012, KON-136): разбор, заглушка «не назначен», фильтры и список РО."""
from __future__ import annotations

import pytest

from app.sync.mapping import EMPLOYEE_FIELDS, F_DEPT_HEAD, map_employee
from app.sync.repo import NO_DEPT_HEAD

from ._sync_fakes import issue
from .test_sync_api import env  # noqa: F401  (фикстура)


def _emp(value="__absent__"):
    fields = {} if value == "__absent__" else {F_DEPT_HEAD: value}
    return map_employee(issue("ANALYTICS-96", "Свободен", **fields))


def test_field_is_requested_from_jira():
    assert F_DEPT_HEAD == "customfield_14401" and F_DEPT_HEAD in EMPLOYEE_FIELDS


def test_dept_head_parsed_name_lowercased_display_stripped():
    row = _emp({"name": "Ivan.Head", "key": "JIRAUSER1", "displayName": "  Имя Фамилия ",
                "emailAddress": "x@example.invalid", "active": True, "timeZone": "Europe/Moscow"})
    assert row.dept_head_login == "ivan.head"
    assert row.dept_head_display_name == "Имя Фамилия"


def test_dept_head_falls_back_to_key_and_allows_missing_display_name():
    row = _emp({"key": "Head.Key"})
    assert row.dept_head_login == "head.key" and row.dept_head_display_name is None


@pytest.mark.parametrize("value", ["__absent__", None, {}, {"name": " "}, "строка", []])
def test_dept_head_absent_or_null_means_no_head(value):
    row = _emp(value)
    assert row.dept_head_login is None and row.dept_head_display_name is None


@pytest.mark.parametrize("login", ["не назначен", "Не  назначен", " НЕ НАЗНАЧЕН "])
def test_placeholder_head_is_treated_as_no_head(login):
    row = _emp({"name": login, "displayName": "Не назначен"})
    assert row.dept_head_login is None and row.dept_head_display_name is None


def test_placeholder_by_display_name_only():
    row = _emp({"name": "stub", "displayName": "Не  назначен"})
    assert row.dept_head_login is None and row.dept_head_display_name is None


def test_employees_api_passes_filters_and_multiple_statuses(env):  # noqa: F811
    client, _, repo, *_ = env
    r = client.get("/sync/employees", params=[
        ("status", "Свободен"), ("status", "Скоро освобождается"), ("dept_head", "ivan.head"),
        ("q", "Иван"), ("include_dismissed", "false"), ("assignable", "true")])
    assert r.status_code == 200
    call = repo.employee_calls[-1]
    assert call["status"] == ["Свободен", "Скоро освобождается"]
    assert call["dept_head"] == "ivan.head" and call["query"] == "Иван"
    assert call["include_dismissed"] is False and call["assignable"] is True


def test_employees_api_defaults_keep_old_behaviour(env):  # noqa: F811
    client, _, repo, *_ = env
    assert client.get("/sync/employees").status_code == 200
    call = repo.employee_calls[-1]
    assert call["status"] is None and call["dept_head"] is None
    assert call["include_dismissed"] is True


def test_employees_api_no_head_value(env):  # noqa: F811
    client, _, repo, *_ = env
    client.get("/sync/employees", params={"dept_head": NO_DEPT_HEAD})
    assert repo.employee_calls[-1]["dept_head"] == NO_DEPT_HEAD


def test_dept_heads_endpoint_returns_list_and_requires_login(env):  # noqa: F811
    client, _, repo, *_ = env
    repo.dept_heads = [{"login": "a.head", "display_name": "А Б"},
                       {"login": "b.head", "display_name": "В Г"}]
    r = client.get("/sync/employees/dept-heads")
    assert r.status_code == 200
    assert r.json() == repo.dept_heads
    client.cookies.clear()
    assert client.get("/sync/employees/dept-heads").status_code == 401


@pytest.mark.parametrize(
    "extra,expected", [({"active": True}, True), ({"active": False}, False), ({}, True)])
def test_dept_head_active_parsed(extra, expected):
    row = _emp({"name": "head.a", "displayName": "Имя", **extra})
    assert row.dept_head_active is expected


def test_dept_head_active_none_without_head():
    assert _emp().dept_head_active is None


def test_hidden_logins_setting_normalized():
    from app.sync.settings import load_sync_settings
    assert load_sync_settings({}).dept_head_hidden_logins == ()
    envv = {"DEPT_HEAD_HIDDEN_LOGINS": " Aa.B, ,cc.d,aa.b "}
    got = load_sync_settings(envv).dept_head_hidden_logins
    assert got == ("aa.b", "cc.d")
