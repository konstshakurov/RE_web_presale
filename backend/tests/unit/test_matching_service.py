"""Сервис matching (KON-38) на настоящем шлюзе LLM и репозитории в памяти: навыки (FR-027),
сопоставление, кэш, пересчёт, недопустимые статусы, внешний провайдер, сбои (FR-005, FR-036)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.llm_gateway import LlmRunner
from app.matching.factory import task_handler
from app.matching.service import MatchingError

from ._matching_fakes import T_NEED2, World

LOGIN = "tester"
NEED = "ANALYTICS-1"


def _world(n=3, **kw) -> World:
    w = World(**kw)
    w.needs.add(NEED)
    for i in range(1, n + 1):
        w.repo.add_employee(f"EMP-{i}", f"Сотрудник-{i:02d}", text=f"Опыт аналитика {i}")
    return w


def _match_calls(w: World) -> list[dict]:
    return [c for c in w.llm_client.calls if c["prompt"].startswith("ПОТРЕБНОСТЬ")]


# --- навыки (FR-027) ---

def test_skills_extracted_once_and_cached_until_file_replaced():
    w = _world(1)
    r = w.svc.schedule_skills("EMP-1", LOGIN)
    assert r.outcome == "scheduled" and r.task_id
    assert w.run_all() == 1
    fid = w.repo.get_main_cv("EMP-1")[0]
    assert w.repo.get_skills(fid) == ("bpmn", "rest api", "sql")
    assert w.repo.get_skill_state(fid).status == "ok"

    again = w.svc.schedule_skills("EMP-1", LOGIN)
    assert again.outcome == "exists" and w.run_all() == 0
    assert len(w.llm_client.calls) == 1  # навыки не пересчитываются без замены файла

    w.repo.set_main_cv("EMP-1", "Новое резюме")
    assert w.svc.schedule_skills("EMP-1", LOGIN).outcome == "scheduled"
    w.run_all()
    assert len(w.llm_client.calls) == 2


def test_skills_failed_extraction_can_be_retried():
    w = _world(1)
    w.skills_reply = lambda p: "не получилось, извините"
    w.svc.schedule_skills("EMP-1", LOGIN)
    w.run_all()
    fid = w.repo.get_main_cv("EMP-1")[0]
    st = w.repo.get_skill_state(fid)
    assert st.status == "failed" and st.error_code == "bad_response"
    assert w.repo.get_skills(fid) == ()

    w.skills_reply = lambda p: '["sql"]'
    assert w.svc.schedule_skills("EMP-1", LOGIN).outcome == "scheduled"
    w.run_all()
    assert w.repo.get_skills(fid) == ("sql",)


def test_skills_no_cv_and_no_provider():
    w = _world(0)
    w.repo.employees["EMP-9"] = dict(name="Без CV", status="Свободен", assignable=True,
                                     note=None, note_author=None, note_updated_at=None, extra=None)
    assert w.svc.schedule_skills("EMP-9", LOGIN).outcome == "no_cv"
    w.repo.add_employee("EMP-1", "Сотрудник-01", text="   ")
    assert w.svc.schedule_skills("EMP-1", LOGIN).outcome == "no_cv"


def test_backfill_schedules_only_missing():
    w = _world(3)
    w.svc.schedule_skills("EMP-1", LOGIN)
    out = w.svc.backfill_skills(LOGIN)
    assert sorted(r.employee_key for r in out if r.outcome == "scheduled") == ["EMP-2", "EMP-3"]


def test_skills_collected_lazily_from_resume_listing():
    w = _world(1)
    w.svc.schedule_skills("EMP-1", LOGIN)
    while w.env.gateway.run_next() is not None:
        pass  # worker выполнил, обработчик не вызывался
    assert w.svc.collect_pending_skills() == 1
    rows, total = w.svc.list_resume(query=None, status=None, tags=["sql"], match_all=True,
                                    limit=10, offset=0)
    assert total == 1 and rows[0].skills == ("bpmn", "rest api", "sql")


# --- сопоставление (FR-005, FR-036) ---

def test_match_async_result_sorted_with_rationale_and_paging():
    w = _world(7)
    st = w.svc.start_match(NEED, LOGIN)
    assert st.status == "running" and st.items == [] and len(st.tasks) == 2  # 5 + 2 кандидата
    assert w.run_all() == 2
    st = w.svc.get_state(NEED, limit=3, offset=0)
    assert st.status == "done" and st.total == 7 and st.candidates_total == 7
    assert [i.percent for i in st.items] == [90, 90, 89]   # по убыванию, ничья - по ФИО
    assert st.items[0].rationale.startswith("обоснование")
    assert st.calculated_at is not None and not st.stale
    rest = w.svc.get_state(NEED, limit=10, offset=3)       # «Показать ещё»
    assert len(rest.items) == 4 and rest.items[0].percent >= rest.items[-1].percent


def test_prompt_contains_no_names_or_keys():
    w = _world(2)
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    body = " ".join(c["prompt"] + (c["system"] or "") for c in _match_calls(w))
    assert "Сотрудник-01" not in body and "EMP-1" not in body
    assert "Опыт аналитика 1" in body and "### K1" in body


def test_repeat_open_and_start_use_cache_without_llm():
    w = _world(3)
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    calls = len(w.llm_client.calls)
    st = w.svc.start_match(NEED, LOGIN)       # повторный «Сопоставить»
    assert w.run_all() == 0 and len(w.llm_client.calls) == calls
    assert st.status == "done" and st.total == 3
    assert w.svc.get_state(NEED, limit=10, offset=0).total == 3


def test_need_change_makes_result_stale_and_recomputes():
    w = _world(2)
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    w.needs.touch(NEED, T_NEED2)
    st = w.svc.get_state(NEED, limit=10, offset=0)
    assert st.stale and st.total == 2          # старый результат виден, помечен
    w.svc.start_match(NEED, LOGIN)
    assert w.run_all() == 1
    assert not w.svc.get_state(NEED, limit=10, offset=0).stale


def test_note_change_makes_result_stale():
    w = _world(1)
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    e = w.repo.employees["EMP-1"]
    e.update(note="не подключать в ЛеманаПРО", note_author="РО",
             note_updated_at=datetime(2026, 10, 11, 10, tzinfo=UTC))
    st = w.svc.get_state(NEED, limit=10, offset=0)
    assert st.stale and st.items[0].note == "не подключать в ЛеманаПРО"
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    assert "не подключать в ЛеманаПРО" in _match_calls(w)[-1]["prompt"]
    assert not w.svc.get_state(NEED, limit=10, offset=0).stale


def test_force_recalculates():
    w = _world(2)
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    w.svc.start_match(NEED, LOGIN, force=True)
    assert w.run_all() == 1 and len(_match_calls(w)) == 2


def test_not_assignable_never_evaluated_and_hidden_after_status_change():
    w = _world(2)
    w.repo.add_employee("EMP-8", "Занятый", status="Коммерческий проект", assignable=False)
    w.repo.add_employee("EMP-9", "Уволенный", status="Уволен", assignable=False)
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    body = " ".join(c["prompt"] for c in _match_calls(w))
    assert body.count("### K") == 2
    st = w.svc.get_state(NEED, limit=10, offset=0)
    assert {i.employee_key for i in st.items} == {"EMP-1", "EMP-2"}
    # статус сменился уже после расчёта: из выдачи пропадает
    w.repo.employees["EMP-1"].update(status="Декрет", assignable=False)
    st = w.svc.get_state(NEED, limit=10, offset=0)
    assert {i.employee_key for i in st.items} == {"EMP-2"}


def test_limit_evaluated_and_preselect_by_skills():
    w = _world(0, max_evaluated=3)
    w.needs.add(NEED, title="Нужен SQL", description="BPMN", requirements=None)
    for i in range(1, 9):
        fid = w.repo.add_employee(f"EMP-{i}", f"Сотрудник-{i:02d}", text=f"CV {i}")
        w.repo.skills[fid] = ["sql", "bpmn"] if i in (6, 7, 8) else ["java"]
    st = w.svc.start_match(NEED, LOGIN)
    assert len(st.tasks) == 1 and st.tasks[0].candidates == 3
    w.run_all()
    st = w.svc.get_state(NEED, limit=10, offset=0)
    assert {i.employee_key for i in st.items} == {"EMP-6", "EMP-7", "EMP-8"}
    assert st.candidates_total == 8 and st.evaluated == 3


def test_second_start_while_running_does_not_duplicate_tasks():
    w = _world(2)
    a = w.svc.start_match(NEED, LOGIN)
    b = w.svc.start_match(NEED, LOGIN, force=True)
    assert [t.task_id for t in a.tasks] == [t.task_id for t in b.tasks]
    assert w.run_all() == 1


def test_bad_response_marks_failed_and_restart_works():
    w = _world(2)
    w.match_reply = lambda p: "Извините, не могу ответить"
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    st = w.svc.get_state(NEED, limit=10, offset=0)
    assert st.status == "failed" and st.items == [] and st.errors and "Извините" not in st.errors[0]
    from ._matching_fakes import reply_for
    w.match_reply = reply_for
    st = w.svc.start_match(NEED, LOGIN)
    assert st.status == "running"
    w.run_all()
    assert w.svc.get_state(NEED, limit=10, offset=0).status == "done"


def test_partial_answer_is_partial_batch_but_done():
    w = _world(3)
    w.match_reply = lambda p: json.dumps([{"id": "K1", "percent": 70, "rationale": "ок"}])
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    st = w.svc.get_state(NEED, limit=10, offset=0)
    assert st.status == "done" and st.total == 1 and st.evaluated == 1


def test_cancelled_task_fails_batch_and_allows_restart():
    w = _world(2)
    st = w.svc.start_match(NEED, LOGIN)
    w.env.gateway.cancel(LOGIN, st.tasks[0].task_id)
    st = w.svc.get_state(NEED, limit=10, offset=0)
    assert st.status == "failed" and st.batches_failed == 1
    assert w.svc.start_match(NEED, LOGIN).status == "running"


def test_unknown_need():
    w = _world(1)
    with pytest.raises(MatchingError) as ei:
        w.svc.start_match("ANALYTICS-404", LOGIN)
    assert ei.value.code == "need_not_found"
    with pytest.raises(MatchingError):
        w.svc.get_state("ANALYTICS-404", limit=10, offset=0)


def test_external_provider_waits_for_confirmation_and_sends_only_after():
    w = _world(2, external_default=True)
    st = w.svc.start_match(NEED, LOGIN)
    assert st.status == "awaiting_confirmation"
    assert st.tasks[0].requires_confirmation and w.llm_client.calls == []
    assert w.run_all() == 0                      # без подтверждения в очередь не попадает
    w.env.gateway.confirm(LOGIN, st.tasks[0].task_id)
    assert w.svc.get_state(NEED, limit=10, offset=0).status == "running"
    w.run_all()
    assert len(w.llm_client.calls) == 1
    assert w.svc.get_state(NEED, limit=10, offset=0).status == "done"


def test_worker_handler_collects_result_without_api_call():
    w = _world(2)
    w.svc.schedule_skills("EMP-1", LOGIN)
    w.svc.start_match(NEED, LOGIN)
    runner = LlmRunner(w.env.gateway, on_finished=task_handler(w.svc))
    while runner.run_once():
        pass
    assert w.repo.pending_batches() == []
    assert w.repo.recs  # кэш заполнен обработчиком worker
    assert w.repo.get_skills(w.repo.get_main_cv("EMP-1")[0]) == ("bpmn", "rest api", "sql")


def test_handler_error_does_not_stop_runner():
    w = _world(1)
    w.svc.start_match(NEED, LOGIN)

    def boom(_task):
        raise RuntimeError("упал обработчик")

    runner = LlmRunner(w.env.gateway, on_finished=boom)
    assert runner.run_once() is True
    # результат всё равно подберёт ленивый сбор при запросе состояния
    assert w.svc.get_state(NEED, limit=10, offset=0).status == "done"
