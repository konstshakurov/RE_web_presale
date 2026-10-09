"""Дети эпика клиента: в кэш попадают только потребности направления аналитики.

Эпик содержит задачи всех направлений (QA-, BACK-, ...). Без сети и БД.
"""
from __future__ import annotations

import pytest

from app.sync import SyncService, SyncSettings
from app.sync.mapping import EPIC_CHILD_FIELDS, NEED_FIELDS, is_need_of, map_need

from ._sync_fakes import FakeJira, FakeStore, FakeSyncRepo, issue

EPIC = "SA-10"


def child(key, project=None, itype=None):
    extra = {"customfield_10101": EPIC}
    if project is not None:
        extra["project"] = {"key": project}
    if itype is not None:
        extra["issuetype"] = {"name": itype}
    return issue(key, **extra)


@pytest.fixture
def env():
    repo = FakeSyncRepo()
    jira = FakeJira()
    service = SyncService(repo, jira, FakeStore({"ivan": "FAKE_t"}), SyncSettings())
    return service, repo, jira


def test_foreign_projects_by_key_prefix_are_not_upserted(env):
    service, repo, jira = env
    jira.children = [child("ANALYTICS-1"), child("QA-2"), child("BACK-3"), child("WEB1C-4"),
                     child("FRONT-5"), child("DVOPS-6"), child("ANALYTICS-7")]
    view = service.get_epic(EPIC, "ivan")
    assert sorted(repo.needs) == ["ANALYTICS-1", "ANALYTICS-7"]
    assert view.needs_total == 2


def test_project_and_issuetype_fields_take_precedence(env):
    service, repo, jira = env
    jira.children = [
        child("ANALYTICS-1", "ANALYTICS", "Аналитик"),
        child("ANALYTICS-2", "ANALYTICS", "Разработчик"),  # другой тип
        child("QA-3", "QA", "Аналитик"),                    # другой проект
        child("ANALYTICS-4", "BACK", None),                 # поле проекта главнее префикса
        child("ANALYTICS-5", None, "Аналитик"),             # проекта нет - префикс
    ]
    service.get_epic(EPIC, "ivan")
    assert sorted(repo.needs) == ["ANALYTICS-1", "ANALYTICS-5"]


def test_epic_children_request_includes_project_and_issuetype(env):
    service, _, jira = env
    service.get_epic(EPIC, "ivan")
    assert {"project", "issuetype"} <= set(jira.children_fields)
    assert set(NEED_FIELDS) <= set(EPIC_CHILD_FIELDS)


def test_needs_total_ignores_foreign_rows_already_in_cache(env):
    service, repo, jira = env
    jira.children = [child("ANALYTICS-1")]
    # чужая строка, загруженная старой версией, счётчик не искажает
    repo.upsert_needs([map_need(child("QA-9"))])
    assert service.get_epic(EPIC, "ivan").needs_total == 1


def test_main_poll_jql_unchanged(env):
    service, repo, jira = env
    repo.set_poller("ivan")
    service.poll("needs")
    call = jira.calls[0]
    assert call[3] == "ANALYTICS" and call[4] == '"Аналитик"' and call[5] == tuple(NEED_FIELDS)


@pytest.mark.parametrize("data,expected", [
    ({"key": "ANALYTICS-1", "fields": {}}, True),
    ({"key": "QA-1", "fields": {}}, False),
    ({"key": "ANALYTICS-1"}, True),
    ({"key": "XANALYTICS-1", "fields": {}}, False),
    ({"fields": {}}, False),
])
def test_is_need_of_prefix(data, expected):
    assert is_need_of(data, "ANALYTICS", "Аналитик") is expected
