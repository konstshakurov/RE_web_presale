"""Опрос Jira (KON-35): окно, пауза без пользователей, истёкшая сессия, отказы, эпики.

Без сети и БД: фейки репозитория и порта Jira.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.integrations.jira import (
    CODE_JIRA_ERROR,
    CODE_JIRA_UNAVAILABLE,
    CODE_NEEDS_REAUTHORIZATION,
    CODE_NO_PERMISSION,
    JiraIntegrationError,
)
from app.sync import SyncError, SyncService, SyncSettings
from app.sync.repo import SCOPE_EMPLOYEES, SCOPE_NEEDS
from app.sync.service import (
    STATE_NEEDS_REAUTH,
    STATE_NEVER,
    STATE_NO_PERMISSION,
    STATE_OK,
    STATE_PAUSED,
    STATE_UNAVAILABLE,
)

from ._sync_fakes import FIXED_NOW, T0, FakeJira, FakeStore, FakeSyncRepo, issue

JIRA_TEXT = "Для этой операции у вас нет прав в проекте ANALYTICS"
FAKE_T = "FAKE_sealed_token_ivan"
FAKE_T2 = "FAKE_sealed_token_petr"


def err(code, text="Ошибка Jira как есть", status=None):
    return JiraIntegrationError(text, code=code, status=status)


def jira_since(moment):
    """Время JQL в поясе Jira (+4)."""
    return (moment + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M")


@pytest.fixture()
def env():
    FIXED_NOW[0] = T0
    repo = FakeSyncRepo()
    jira = FakeJira()
    store = FakeStore({"ivan": FAKE_T, "petr": FAKE_T2})
    clock = {"now": T0}
    service = SyncService(repo, jira, store, SyncSettings(), now=lambda: clock["now"])
    return service, repo, jira, clock


def test_no_active_users_polling_is_paused_and_jira_not_called(env):
    service, repo, jira, _ = env
    res = service.poll(SCOPE_NEEDS)
    assert res.paused and not res.ok and jira.calls == []
    info = service.sync_info(SCOPE_NEEDS)
    assert info.state == STATE_PAUSED and info.paused and info.stale


def test_first_poll_loads_six_months_with_only_needed_fields(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    jira.issues = [issue("ANALYTICS-1"), issue("ANALYTICS-2")]
    res = service.poll(SCOPE_NEEDS)
    assert res.ok and res.upserted == 2 and set(repo.needs) == {"ANALYTICS-1", "ANALYTICS-2"}
    _, token, since, project, itype, fields = jira.calls[0]
    assert token == FAKE_T and project == "ANALYTICS" and itype == '"Аналитик"'
    assert since == jira_since(T0 - timedelta(days=183))
    assert "parent" not in fields and "customfield_10101" in fields
    assert repo.states[SCOPE_NEEDS].initial_load_done


def test_next_poll_is_incremental_with_one_minute_overlap(env):
    service, repo, jira, clock = env
    repo.set_poller("ivan")
    service.poll(SCOPE_NEEDS)  # первичная загрузка, курсор = T0
    clock["now"] = T0 + timedelta(minutes=5)
    service.poll(SCOPE_NEEDS)
    assert jira.calls[-1][2] == jira_since(T0 - timedelta(minutes=1))


def test_cursor_does_not_pass_newest_cached_row_when_clocks_differ(env):
    """Часы сервера впереди Jira: курсор не уходит дальше самой свежей строки кэша."""
    service, repo, jira, clock = env
    repo.set_poller("ivan")
    jira.issues = [issue("ANALYTICS-1", updated="2026-10-09T10:00:00.000+0000")]
    service.poll(SCOPE_NEEDS)
    clock["now"] = T0 + timedelta(minutes=5)
    service.poll(SCOPE_NEEDS)
    # min(12:00 UTC, 10:00 UTC) - 1 мин = 09:59 UTC = 13:59 по Jira
    assert jira.calls[-1][2] == "2026-10-09 13:59"


def test_repeated_poll_does_not_duplicate(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    jira.issues = [issue("ANALYTICS-1"), issue("ANALYTICS-2")]
    service.poll(SCOPE_NEEDS)
    service.poll(SCOPE_NEEDS)
    assert len(repo.needs) == 2


def test_expired_session_marks_needs_reauth_and_stops_hitting_jira(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    jira.error = err(CODE_NEEDS_REAUTHORIZATION, "Сессия Jira истекла", 401)
    res = service.poll(SCOPE_NEEDS)
    assert not res.ok and res.code == STATE_NEEDS_REAUTH
    info = service.sync_info(SCOPE_NEEDS)
    assert info.state == STATE_NEEDS_REAUTH and info.stale
    assert info.message == "Сессия Jira истекла"
    calls = len(jira.calls)
    service.poll(SCOPE_NEEDS)  # токен отвергнут: Jira не дёргаем до повторного входа
    assert len(jira.calls) == calls
    assert service.sync_info(SCOPE_NEEDS).state == STATE_NEEDS_REAUTH


def test_relogin_changes_secret_and_polling_resumes(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    jira.error = err(CODE_NEEDS_REAUTHORIZATION, "Сессия Jira истекла", 401)
    service.poll(SCOPE_NEEDS)
    jira.error = None
    jira.issues = [issue("ANALYTICS-1")]
    repo.set_poller("ivan", T0 + timedelta(minutes=1))  # новый токен в secret_store
    assert service.poll(SCOPE_NEEDS).ok
    assert service.sync_info(SCOPE_NEEDS).state == STATE_OK


def test_falls_back_to_other_active_user_when_one_session_expired(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    repo.set_poller("petr")
    jira.errors_by_token[FAKE_T] = err(CODE_NEEDS_REAUTHORIZATION, "истекла", 401)
    jira.errors_by_token[FAKE_T2] = None
    jira.issues = [issue("ANALYTICS-1")]
    res = service.poll(SCOPE_NEEDS)
    assert res.ok and {c[1] for c in jira.calls} == {FAKE_T, FAKE_T2}


def test_no_permission_is_distinct_and_text_is_verbatim(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    jira.error = err(CODE_NO_PERMISSION, JIRA_TEXT, 403)
    res = service.poll(SCOPE_NEEDS)
    assert res.code == STATE_NO_PERMISSION and res.message == JIRA_TEXT
    info = service.sync_info(SCOPE_NEEDS)
    assert info.state == STATE_NO_PERMISSION and info.message == JIRA_TEXT


def test_jira_unavailable_is_distinct_and_does_not_burn_tokens(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    repo.set_poller("petr")
    jira.error = err(CODE_JIRA_UNAVAILABLE, "Jira не отвечает", 503)
    res = service.poll(SCOPE_NEEDS)
    assert res.code == STATE_UNAVAILABLE and len(jira.calls) == 1  # перебор остановлен
    assert service.sync_info(SCOPE_NEEDS).state == STATE_UNAVAILABLE
    jira.error = None  # Jira вернулась, токен цел: следующий опрос успешен
    assert service.poll(SCOPE_NEEDS).ok
    assert service.sync_info(SCOPE_NEEDS).state == STATE_OK


def test_other_jira_refusal_text_shown_as_is(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    text = "The value 'X' does not exist for the field 'issuetype'."
    jira.error = err(CODE_JIRA_ERROR, text, 400)
    service.poll(SCOPE_NEEDS)
    info = service.sync_info(SCOPE_NEEDS)
    assert info.code == "jira_error" and info.message == text


def test_message_in_state_is_stripped_of_secrets(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    jira.error = err(CODE_JIRA_ERROR, "Ошибка: password=FAKE_hunter2_value JSESSIONID=ABCDEF123")
    service.poll(SCOPE_NEEDS)
    msg = service.sync_info(SCOPE_NEEDS).message
    assert "hunter2" not in msg and "ABCDEF123" not in msg


def test_internal_failure_is_recorded_without_leaking_details(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    jira.error = RuntimeError("boom FAKE_internal_detail")
    service.poll(SCOPE_NEEDS)
    info = service.sync_info(SCOPE_NEEDS)
    assert info.state == "error" and "internal_detail" not in info.message


def test_failed_poll_keeps_cache_and_last_success_stamp(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    jira.issues = [issue("ANALYTICS-1")]
    service.poll(SCOPE_NEEDS)
    stamp = repo.states[SCOPE_NEEDS].last_success_at
    jira.error = err(CODE_JIRA_UNAVAILABLE, "Jira не отвечает")
    service.poll(SCOPE_NEEDS)
    info = service.sync_info(SCOPE_NEEDS)
    assert info.last_success_at == stamp and "ANALYTICS-1" in repo.needs and info.stale


def test_never_synced_state(env):
    service, repo, *_ = env
    repo.set_poller("ivan")
    assert service.sync_info(SCOPE_NEEDS).state == STATE_NEVER


def test_manual_refresh_uses_users_own_token_and_works_without_pollers(env):
    service, repo, jira, _ = env
    jira.issues = [issue("ANALYTICS-1")]
    res = service.refresh(SCOPE_NEEDS, "petr")
    assert res.ok and jira.calls[0][1] == FAKE_T2


def test_manual_refresh_errors_carry_jira_text(env):
    service, repo, jira, _ = env
    jira.error = err(CODE_NO_PERMISSION, JIRA_TEXT, 403)
    res = service.refresh(SCOPE_NEEDS, "ivan")
    assert not res.ok and res.code == STATE_NO_PERMISSION and res.message == JIRA_TEXT


def test_refresh_without_token_asks_for_reauth(env):
    service, *_ = env
    assert service.refresh(SCOPE_NEEDS, "nobody").code == STATE_NEEDS_REAUTH


def test_second_run_while_first_is_running_is_busy(env):
    service, repo, jira, _ = env
    repo.busy.add(SCOPE_NEEDS)
    res = service.refresh(SCOPE_NEEDS, "ivan")
    assert res.code == "busy" and jira.calls == []


def test_employees_sync_full_list_and_assignable(env):
    service, repo, jira, _ = env
    repo.set_poller("ivan")
    jira.issues = [issue("ANALYTICS-96", "Свободен"), issue("ANALYTICS-97", "Уволен")]
    res = service.poll(SCOPE_EMPLOYEES)
    assert res.ok and repo.employees["ANALYTICS-97"].assignable is False
    _, _, since, project, itype, _ = jira.calls[0]
    assert since.startswith("2000-01-01") and project == "ANALYTICS" and itype == '"Epic"'


def test_without_jira_integration_state_not_enabled():
    repo = FakeSyncRepo()
    service = SyncService(repo, None, FakeStore({"ivan": FAKE_T}), SyncSettings())
    repo.set_poller("ivan")
    res = service.poll(SCOPE_NEEDS)
    assert res.code == "not_enabled"
    assert service.sync_info(SCOPE_NEEDS).state == "not_enabled"


def test_employees_not_enabled_without_cv_pool():
    repo = FakeSyncRepo(with_cv=False)
    service = SyncService(repo, FakeJira(), FakeStore({"ivan": FAKE_T}), SyncSettings())
    repo.set_poller("ivan")
    assert service.poll(SCOPE_EMPLOYEES).code == "not_enabled"
    assert service.sync_info(SCOPE_EMPLOYEES).state == "not_enabled"


# --- эпики ---

def test_epic_opened_loads_rules_and_children_via_agile_api(env):
    service, repo, jira, _ = env
    jira.children = [issue("ANALYTICS-11", customfield_10101="SA-10"), issue("ANALYTICS-12")]
    view = service.get_epic("SA-10", "ivan")
    assert view.name == "Клиент Альфа"
    assert view.rules == [{"id": "customfield_14205", "name": "NDA", "value": "Есть NDA"}]
    assert not view.stale and view.needs_total == 2  # у второго связь дал Agile API
    assert [c[0] for c in jira.calls] == ["epic", "issue", "children"]


def test_epic_ttl_one_hour_uses_cache_then_refreshes(env):
    service, repo, jira, clock = env
    service.get_epic("SA-10", "ivan")
    n = len(jira.calls)
    clock["now"] = T0 + timedelta(minutes=59)
    assert not service.get_epic("SA-10", "ivan").stale and len(jira.calls) == n
    clock["now"] = T0 + timedelta(minutes=61)
    service.get_epic("SA-10", "ivan")
    assert len(jira.calls) == n + 3
    service.get_epic("SA-10", "ivan", force=True)
    assert len(jira.calls) == n + 6


def test_epic_jira_refusal_serves_cache_with_mark(env):
    service, repo, jira, clock = env
    service.get_epic("SA-10", "ivan")
    clock["now"] = T0 + timedelta(hours=2)
    jira.error = err(CODE_JIRA_UNAVAILABLE, "Jira не отвечает", 503)
    view = service.get_epic("SA-10", "ivan")
    assert view.stale and view.error.code == STATE_UNAVAILABLE and view.rules


def test_epic_jira_refusal_without_cache_raises_text_as_is(env):
    service, repo, jira, _ = env
    jira.error = err(CODE_NO_PERMISSION, JIRA_TEXT, 403)
    with pytest.raises(SyncError) as e:
        service.get_epic("SA-10", "ivan")
    assert e.value.code == STATE_NO_PERMISSION and e.value.message == JIRA_TEXT


def test_epic_bad_key_rejected_without_calling_jira(env):
    service, repo, jira, _ = env
    with pytest.raises(SyncError):
        service.get_epic("../etc", "ivan")
    assert jira.calls == []
