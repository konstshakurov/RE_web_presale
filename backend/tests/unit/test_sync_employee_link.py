"""Связь потребности с сотрудником и поля FR-002 (KON-61): mapping и сервис, без БД и сети."""
from __future__ import annotations

import pytest

from app.sync import SyncService, SyncSettings
from app.sync.mapping import EMPLOYEE_FIELDS, NEED_FIELDS, map_employee, map_need
from app.sync.repo import SCOPE_EMPLOYEES, SCOPE_NEEDS

from ._sync_fakes import FIXED_NOW, T0, FakeJira, FakeStore, FakeSyncRepo, issue

FAKE_T = "FAKE_sealed_token_ivan"


def emp(key, **fields):
    return issue(key, "Свободен", **fields)


def test_fields_requested_from_jira():
    assert "customfield_10707" in NEED_FIELDS
    for f in ("customfield_10707", "customfield_10814", "customfield_13504"):
        assert f in EMPLOYEE_FIELDS
    for f in ("customfield_10815", "customfield_10816", "description"):
        assert f not in EMPLOYEE_FIELDS


def test_need_login_comes_from_profile_field_lowercase():
    row = map_need(issue("ANALYTICS-1", customfield_10707={"name": "Ivan.Ivanov", "key": "x"}))
    assert row.employee_login == "ivan.ivanov" and row.employee_key is None


@pytest.mark.parametrize("value", [None, "", {}, {"displayName": "Без логина"}, "ivan.ivanov"])
def test_need_without_user_object_has_no_login(value):
    assert map_need(issue("ANALYTICS-1", customfield_10707=value)).employee_login is None


def test_numeric_user_id_field_is_not_used_as_login():
    row = map_need(issue("ANALYTICS-1", customfield_11515="902393"))
    assert row.employee_login is None


@pytest.mark.parametrize("value", [
    "Планируется в ближайшие 3 месяца", "В ближайшие 3 месяца не планируется", "не указано"])
def test_employee_login_and_vacation_plan_stored_as_is(value):
    row = map_employee(emp(
        "ANALYTICS-9", customfield_10707={"name": "Petr.Petrov"},
        customfield_10814={"value": value}, customfield_13504="Опыт"))
    assert row.jira_login == "petr.petrov" and row.vacation_plan == value
    assert row.extra_experience == "Опыт"


def test_vacation_plan_empty_or_too_long_is_none_and_row_survives():
    assert map_employee(emp("ANALYTICS-9")).vacation_plan is None
    row = map_employee(emp("ANALYTICS-9", customfield_10814={"value": "я" * 129}))
    assert row is not None and row.vacation_plan is None


@pytest.fixture()
def env():
    FIXED_NOW[0] = T0
    repo, jira = FakeSyncRepo(), FakeJira()
    repo.set_poller("ivan")
    service = SyncService(repo, jira, FakeStore({"ivan": FAKE_T}), SyncSettings(),
                          now=lambda: T0)
    return service, repo, jira


def profile(login):
    return {"customfield_10707": {"name": login}}


def test_need_gets_employee_key_when_single_card_matches(env):
    service, repo, jira = env
    jira.issues = [emp("ANALYTICS-100", **profile("anna.a"))]
    service.poll(SCOPE_EMPLOYEES)
    jira.issues = [issue("ANALYTICS-1", **profile("anna.a")),
                   issue("ANALYTICS-2", **profile("nobody")), issue("ANALYTICS-3")]
    service.poll(SCOPE_NEEDS)
    keys = {k: n.employee_key for k, n in repo.needs.items()}
    assert keys == {"ANALYTICS-1": "ANALYTICS-100", "ANALYTICS-2": None, "ANALYTICS-3": None}


def test_ambiguous_login_stays_null(env):
    service, repo, jira = env
    jira.issues = [emp("ANALYTICS-100", **profile("dup")), emp("ANALYTICS-101", **profile("dup"))]
    service.poll(SCOPE_EMPLOYEES)
    jira.issues = [issue("ANALYTICS-1", **profile("dup"))]
    service.poll(SCOPE_NEEDS)
    assert repo.needs["ANALYTICS-1"].employee_key is None
    assert repo.needs["ANALYTICS-1"].employee_login == "dup"


def test_employee_sync_backfills_existing_needs_and_clears_when_ambiguous(env):
    service, repo, jira = env
    jira.issues = [issue("ANALYTICS-1", **profile("anna.a"))]
    service.poll(SCOPE_NEEDS)  # карточек ещё нет
    assert repo.needs["ANALYTICS-1"].employee_key is None
    jira.issues = [emp("ANALYTICS-100", **profile("anna.a"))]
    service.poll(SCOPE_EMPLOYEES)
    assert repo.needs["ANALYTICS-1"].employee_key == "ANALYTICS-100"
    jira.issues = [emp("ANALYTICS-100", **profile("anna.a")),
                   emp("ANALYTICS-102", **profile("anna.a"))]
    service.poll(SCOPE_EMPLOYEES)
    assert repo.needs["ANALYTICS-1"].employee_key is None


def test_cv_unavailable_does_not_break_needs_sync(env):
    service, repo, jira = env

    def broken():
        raise RuntimeError("cv down")

    repo.employee_login_map = broken
    jira.issues = [issue("ANALYTICS-1", **profile("anna.a"))]
    res = service.poll(SCOPE_NEEDS)
    assert res.ok and repo.needs["ANALYTICS-1"].employee_key is None
