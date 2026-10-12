"""Двухступенчатая оценка (KON-38): скрининг по навыкам всех допустимых, глубокая оценка топ-N,
«Оценить следующих», кэш, один предпросмотр и одно подтверждение на прогон для внешнего
провайдера (FR-005, FR-032, FR-023). Шлюз настоящий, клиент LLM и репозитории - подмены.
Сотрудники и тексты CV придуманы для теста."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.llm_gateway import LlmRunner
from app.matching.factory import task_handler
from app.matching.service import MatchingError, SearchFilters

from ._matching_fakes import T_NEED2, World

LOGIN = "tester"
NEED = "ANALYTICS-1"
QUERY = "Нужен аналитик с опытом SQL и BPMN"


def _by_skills(prompt: str) -> str:
    """«Модель»: 90% тем, у кого в навыках sql, иначе 10%; ответ по меткам запроса."""
    out, label = [], None
    for ln in prompt.splitlines():
        if ln.startswith("### "):
            label = ln[4:].strip()
        elif ln.startswith("Навыки из CV:") and label:
            out.append({"id": label, "percent": 90 if "sql" in ln else 10,
                        "rationale": f"обоснование {label}"})
    return json.dumps(out, ensure_ascii=False)


def _world(n=8, *, deep=3, screen_batch=50, sql=(6, 7, 8), **kw) -> World:
    """n сотрудников с навыками; у номеров из ``sql`` есть sql, у остальных java."""
    w = World(max_evaluated=deep, screen_batch=screen_batch, **kw)
    w.needs.add(NEED, title="Нужен аналитик SQL", description="BPMN", requirements=None)
    for i in range(1, n + 1):
        fid = w.repo.add_employee(f"EMP-{i}", f"Сотрудник-{i:02d}", text=f"Резюме номер {i}")
        w.repo.skills[fid] = ["sql", "bpmn"] if i in sql else ["java"]
        w.repo.states[fid] = dict(status="ok", task_id=None, model="m", by="x", code=None,
                                  msg=None, at=datetime(2026, 9, 1, tzinfo=UTC))
    w.match_reply = _by_skills
    return w


def _calls(w: World, stage: str) -> list[dict]:
    """Вызовы LLM сопоставления: screening - без текста CV, deep - с текстом CV."""
    deep = stage == "deep"
    return [c for c in w.llm_client.calls
            if c["prompt"].startswith("ПОТРЕБНОСТЬ") and ("Текст CV:" in c["prompt"]) == deep]


def _keys(st) -> set[str]:
    return {i.employee_key for i in st.items}


def _state(w: World, **kw):
    return w.svc.get_state(NEED, limit=kw.pop("limit", 100), offset=0)


# --- ступень 1: скрининг ---

def test_screening_prompt_has_only_skills_no_cv_text_names_or_keys():
    w = _world(5, deep=2)
    w.repo.employees["EMP-1"].update(note="Не подключать к клиенту", note_author="РО")
    st = w.svc.start_match(NEED, LOGIN)
    assert st.stage == "screening" and len(st.tasks) == 1 and st.tasks[0].candidates == 5
    w.env.gateway.run_next()
    [call] = w.llm_client.calls
    prompt = call["prompt"]
    assert prompt.count("### K") == 5
    for secret in ("Резюме номер", "Сотрудник-", "EMP-", "Не подключать", "РО"):
        assert secret not in prompt
    assert "Навыки из CV: java" in prompt and "Текст CV:" not in prompt
    assert "Нужен аналитик SQL" in prompt                  # потребность - данные запроса


def test_screening_batches_follow_admin_setting():
    w = _world(25, deep=5, screen_batch=10, sql=())
    st = w.svc.start_match(NEED, LOGIN)
    assert sorted(t.candidates for t in st.tasks) == [5, 10, 10]
    assert {t.stage for t in st.tasks} == {"screening"}
    assert len({t.run_id for t in st.tasks}) == 1 and st.run_id == st.tasks[0].run_id
    w.settings.set(screen_batch=50)                         # новое значение - для новых прогонов
    assert w.svc.settings() == (5, 50)


def test_up_to_n_candidates_skip_screening():
    w = _world(3, deep=3)
    st = w.svc.start_match(NEED, LOGIN)
    assert st.stage == "deep" and st.tasks[0].candidates == 3
    w.run_all()
    assert _calls(w, "screening") == [] and len(_calls(w, "deep")) == 1
    assert _state(w).evaluated == 3 and _state(w).remaining == 0


def test_screening_then_deep_top_n_automatically_with_internal_model():
    w = _world()
    w.svc.start_match(NEED, LOGIN)
    assert w.run_all() >= 2                               # скрининг, затем (цепочкой) глубокая
    st = _state(w)
    assert st.status == "done" and st.stage == "deep"
    assert _keys(st) == {"EMP-6", "EMP-7", "EMP-8"}
    assert st.candidates_total == 8 and st.evaluated == 3 and st.remaining == 5
    assert st.screened == 8 and st.screen_total == 8 and st.deep_size == 3
    assert st.can_evaluate_next is True
    assert len(_calls(w, "screening")) == 1 and len(_calls(w, "deep")) == 1
    deep = _calls(w, "deep")[0]["prompt"]
    assert "Резюме номер 6" in deep and "Резюме номер 1" not in deep   # текст - только у топа


def test_candidate_without_skills_stays_in_ranking_by_deterministic_score():
    w = _world(6, deep=3, sql=(5, 6))
    w.needs.add(NEED, title="Нужен аналитик SQL", description="BPMN REST", requirements=None)
    fid = w.repo.add_employee("EMP-9", "Сотрудник-09", text="Аналитик: SQL, BPMN, REST, нужен")
    assert w.repo.skills.get(fid) is None               # навыки не выделены
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    [screen] = _calls(w, "screening")
    assert screen["prompt"].count("### K") == 6          # безнавыковый в LLM скрининга не ушёл
    st = _state(w)
    assert "EMP-9" in _keys(st) and st.screen_total == 6 and st.candidates_total == 7
    assert st.screened == 6


def test_all_screening_batches_failed_stops_before_stage_two():
    w = _world()
    calls = {"n": 0}

    def reply(prompt):
        calls["n"] += 1
        return "извините, не могу" if "Текст CV:" not in prompt else _by_skills(prompt)

    w.match_reply = reply
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    st = _state(w)
    # все пакеты скрининга упали: ступень 2 не запускается, статус виден, кэш скрининга пуст
    assert st.status == "failed" and st.screened == 0 and st.evaluated == 0
    assert st.errors and "не разобран" in st.errors[0]
    assert _calls(w, "deep") == []


def test_partial_screening_answer_keeps_unrated_deterministic():
    w = _world()

    def reply(prompt):
        data = json.loads(_by_skills(prompt))
        return json.dumps(data[:5]) if "Текст CV:" not in prompt else json.dumps(data)

    w.match_reply = reply
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    st = _state(w)
    assert st.screened == 5 and st.status == "done" and st.evaluated == 3


# --- кэш и stale ---

def test_second_start_uses_screen_and_deep_cache_without_llm():
    w = _world()
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    n = len(w.llm_client.calls)
    st = w.svc.start_match(NEED, LOGIN)
    assert w.env.gateway.run_next() is None and len(w.llm_client.calls) == n
    assert st.status == "done" and st.evaluated == 3


def test_need_change_makes_screening_stale_and_recomputes_both_stages():
    w = _world()
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    w.needs.touch(NEED, T_NEED2)
    assert _state(w).stale is True
    st = w.svc.start_match(NEED, LOGIN)
    assert st.stage == "screening"                        # скрининг по прежней версии не свежий
    w.run_all()
    assert len(_calls(w, "screening")) == 2 and len(_calls(w, "deep")) == 2
    assert _state(w).stale is False


def test_new_employee_is_screened_alone_and_cached_ones_are_not_resent():
    w = _world()
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    fid = w.repo.add_employee("EMP-20", "Сотрудник-20", text="Резюме номер 20")
    w.repo.skills[fid] = ["sql"]
    st = w.svc.start_match(NEED, LOGIN)
    assert st.stage == "screening" and st.tasks[0].candidates == 1
    w.run_all()
    assert _calls(w, "screening")[1]["prompt"].count("### K") == 1
    st = _state(w)                                         # 90% при равенстве - по ФИО: четвёртый
    assert _keys(st) == {"EMP-6", "EMP-7", "EMP-8"} and st.screened == 9
    assert len(_calls(w, "deep")) == 1                     # топ-3 уже в кэше глубокой оценки


def test_force_recomputes_screening_and_top_n():
    w = _world()
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    w.svc.start_match(NEED, LOGIN, force=True)
    w.run_all()
    assert len(_calls(w, "screening")) == 2 and len(_calls(w, "deep")) == 2


# --- «Оценить следующих» ---

def test_evaluate_next_takes_following_portion_without_recomputing_evaluated():
    w = _world()
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    before = len(_calls(w, "deep"))
    st = w.svc.evaluate_next(NEED, LOGIN)
    assert st.stage == "deep" and st.tasks[0].candidates == 3
    w.run_all()
    st = _state(w)
    assert st.evaluated == 6 and st.remaining == 2 and len(_calls(w, "deep")) == before + 1
    sent = _calls(w, "deep")[-1]["prompt"]
    assert not any(f"Резюме номер {i}\n" in sent or sent.endswith(f"Резюме номер {i}") for i in ())
    for k in ("EMP-6", "EMP-7", "EMP-8"):                  # первые порции не отправлялись
        assert f"Резюме номер {k[-1]}" not in sent
    assert w.svc.evaluate_next(NEED, LOGIN) is not None
    w.run_all()
    st = _state(w)
    assert st.evaluated == 8 and st.remaining == 0 and st.can_evaluate_next is False
    n = len(w.llm_client.calls)
    w.svc.evaluate_next(NEED, LOGIN)                       # всё оценено: LLM не вызывается
    assert w.env.gateway.run_next() is None and len(w.llm_client.calls) == n


def test_evaluate_next_does_not_start_second_run_while_running():
    w = _world()
    w.svc.start_match(NEED, LOGIN)
    w.run_all()
    a = w.svc.evaluate_next(NEED, LOGIN)
    b = w.svc.evaluate_next(NEED, LOGIN)
    assert [t.task_id for t in a.tasks] == [t.task_id for t in b.tasks]
    assert b.can_evaluate_next is False                    # пока идёт прогон
    assert w.run_all() == 1


def test_chain_is_idempotent_for_worker_and_api_collectors():
    w = _world()
    w.svc.start_match(NEED, LOGIN)
    task = w.env.gateway.run_next()                        # скрининг выполнен
    for _ in range(3):                                     # worker и опросы API подряд
        w.svc.collect_task(task.id)
        _state(w)
    deep_runs = {b["run_id"] for b in w.repo.batches if b["stage"] == "deep"}
    assert len(deep_runs) == 1 and len(w.repo.batches) == 1
    w.run_all()
    assert len(_calls(w, "deep")) == 1


def test_worker_handler_drives_both_stages_without_api_calls():
    w = _world()
    w.svc.start_match(NEED, LOGIN)
    runner = LlmRunner(w.env.gateway, on_finished=task_handler(w.svc))
    while runner.run_once():
        pass
    assert w.repo.batches[0]["stage"] == "deep" and w.repo.batches[0]["status"] == "done"
    assert len(w.repo.recs) == 3 and len(w.repo.mscreens) == 8


# --- внешний провайдер: один предпросмотр и одно подтверждение на прогон ---

def _confirm_run(w: World, st) -> None:
    w.svc.confirm_run(LOGIN, st.run_id)


def test_external_screening_run_is_one_preview_and_one_confirm_for_all_batches():
    w = _world(25, deep=5, screen_batch=10, sql=(21, 22, 23, 24, 25), external_default=True)
    st = w.svc.start_match(NEED, LOGIN)
    assert st.status == "awaiting_confirmation" and st.stage == "screening"
    assert len(st.tasks) == 3 and all(t.requires_confirmation for t in st.tasks)
    assert w.llm_client.calls == [] and w.run_all() == 0

    info = w.svc.run_info(LOGIN, st.run_id)
    assert info.kind == "match" and info.stage == "screening" and info.is_external
    assert info.requires_confirmation and info.candidates == 25 and len(info.tasks) == 3
    assert all(t.prompt and "### K1" in t.prompt for t in info.tasks)   # обезличенный текст
    assert all("EMP-" not in t.prompt and "Сотрудник-" not in t.prompt for t in info.tasks)
    assert w.llm_client.calls == []

    done = w.svc.confirm_run(LOGIN, st.run_id)             # один confirm - все три пакета
    assert done.status == "running" and not done.requires_confirmation
    w.env.gateway.run_next()
    assert all(not t.requires_confirmation for t in w.svc.run_info(LOGIN, st.run_id).tasks)


def test_external_deep_stage_needs_its_own_preview_and_confirm_then_next_again():
    w = _world(deep=3, external_default=True)
    st = w.svc.start_match(NEED, LOGIN)
    screening_run = st.run_id
    w.svc.confirm_run(LOGIN, screening_run)
    w.run_all()                                            # скрининг выполнен, ступень 2 поставлена
    st = _state(w)
    assert st.status == "awaiting_confirmation" and st.stage == "deep"
    assert st.run_id and st.run_id != screening_run
    n = len(w.llm_client.calls)
    assert n == 1 and w.run_all() == 0                     # ступень 2 без подтверждения не уходит
    info = w.svc.run_info(LOGIN, st.run_id)
    assert info.stage == "deep" and info.requires_confirmation and info.candidates == 3
    w.svc.confirm_run(LOGIN, st.run_id)
    w.run_all()
    st = _state(w)
    assert st.status == "done" and st.evaluated == 3 and len(w.llm_client.calls) == 2

    nxt = w.svc.evaluate_next(NEED, LOGIN)                 # «Оценить следующих»: новый прогон
    assert nxt.status == "awaiting_confirmation" and nxt.run_id not in (screening_run, st.run_id)
    assert len(w.llm_client.calls) == 2
    w.svc.confirm_run(LOGIN, nxt.run_id)
    w.run_all()
    assert _state(w).evaluated == 6


def test_confirm_run_errors_owner_and_repeat():
    w = _world(deep=3, external_default=True)
    st = w.svc.start_match(NEED, LOGIN)
    with pytest.raises(MatchingError) as e:
        w.svc.run_info("someone-else", st.run_id)          # чужой прогон не виден
    assert e.value.code == "run_not_found"
    with pytest.raises(MatchingError):
        w.svc.run_info(LOGIN, "not-a-uuid")
    with pytest.raises(MatchingError):
        w.svc.run_info(LOGIN, "00000000-0000-4000-8000-000000000000")
    w.svc.confirm_run(LOGIN, st.run_id)
    with pytest.raises(Exception) as e2:                   # повтор: подтверждать нечего
        w.svc.confirm_run(LOGIN, st.run_id)
    assert getattr(e2.value, "code", "") == "not_awaiting_confirmation"


def test_individual_task_confirmation_still_works_and_run_confirm_takes_the_rest():
    w = _world(25, deep=5, screen_batch=10, sql=(), external_default=True)
    st = w.svc.start_match(NEED, LOGIN)
    w.env.gateway.confirm(LOGIN, st.tasks[0].task_id)      # прежний путь /llm/tasks/{id}/confirm
    info = w.svc.run_info(LOGIN, st.run_id)
    assert sum(t.requires_confirmation for t in info.tasks) == 2
    w.svc.confirm_run(LOGIN, st.run_id)
    assert w.svc.run_info(LOGIN, st.run_id).requires_confirmation is False


def test_cancel_run_cancels_pending_tasks_and_allows_restart():
    w = _world(deep=3, external_default=True)
    st = w.svc.start_match(NEED, LOGIN)
    info = w.svc.cancel_run(LOGIN, st.run_id)
    assert info.status == "failed" and not info.requires_confirmation
    assert _state(w).status == "failed" and w.llm_client.calls == []
    again = w.svc.start_match(NEED, LOGIN)
    assert again.status == "awaiting_confirmation" and again.run_id != st.run_id


def test_anonymization_failure_at_stage_two_is_logged_and_does_not_break_polling(monkeypatch):
    w = _world()
    w.svc.start_match(NEED, LOGIN)
    from app.llm_gateway import AnonymizationError

    def boom(*a, **kw):
        raise AnonymizationError("осталось 1 фрагментов")

    monkeypatch.setattr(w.svc._gw, "submit", boom)
    task = w.env.gateway.run_next()
    w.svc.collect_task(task.id)                            # ступень 2 не поставлена, без исключения
    st = _state(w)
    assert st.status == "done" and st.evaluated == 0 and st.screened == 8
    monkeypatch.undo()
    assert st.can_evaluate_next is True                    # «Оценить следующих» поставит повторно


# --- настройки ---

def test_settings_are_validated_and_applied_to_next_run():
    w = _world()
    assert w.svc.update_settings(10, None) == (10, 50)
    assert w.svc.update_settings(None, 20) == (10, 20)
    for bad in ((4, None), (101, None), (None, 9), (None, 101)):
        with pytest.raises(MatchingError) as e:
            w.svc.update_settings(*bad)
        assert e.value.code == "bad_settings"
    assert w.svc.settings() == (10, 20)
    st = w.svc.start_match(NEED, LOGIN)                    # 8 <= N=10: скрининг не нужен
    assert st.stage == "deep" and st.deep_size == 10


# --- поиск по смыслу ---

def _search_world(**kw) -> World:
    w = World(**kw)
    texts = ["Банковская сфера, интеграции REST, SQL", "Страхование, BPMN, Visio",
             "Интеграции REST, Kafka, банковский сектор", "SQL, отчётность, банк",
             "BPMN, UML, телеком", "REST, SQL, финтех"]
    for i, text in enumerate(texts, 1):
        fid = w.repo.add_employee(f"EMP-{i}", f"Сотрудник-{i:02d}", text=text)
        w.repo.skills[fid] = ["sql", "rest"] if i in (1, 4, 6) else ["bpmn"]
        w.repo.states[fid] = dict(status="ok", task_id=None, model="m", by="x", code=None,
                                  msg=None, at=datetime(2026, 9, 1, tzinfo=UTC))
    w.match_reply = _by_skills
    return w


def _search_calls(w: World, deep: bool) -> list[dict]:
    return [c for c in w.llm_client.calls if c["prompt"].startswith("ЗАПРОС РУКОВОДИТЕЛЯ")
            and ("Текст CV:" in c["prompt"]) == deep]


def test_search_two_stages_next_portion_and_cache():
    w = _search_world(max_evaluated=2)
    q = "аналитик sql rest"
    st = w.svc.start_search(LOGIN, q, limit=50)
    assert st.stage == "screening" and st.tasks[0].candidates == 6
    w.run_all()
    got = w.svc.get_search(LOGIN, q, limit=50, offset=0)
    assert got.stage == "deep" and got.status == "done" and got.evaluated == 2
    assert {i.employee_key for i in got.items if i.source == "llm"} <= {"EMP-1", "EMP-4", "EMP-6"}
    assert got.screened == 6 and got.remaining == 4 and got.can_evaluate_next is True
    assert all(i.screen_percent is not None for i in got.items if i.source == "screening")
    n = len(w.llm_client.calls)

    again = w.svc.start_search(LOGIN, q, limit=50)         # кэш: ни скрининга, ни глубокой
    assert w.env.gateway.run_next() is None and len(w.llm_client.calls) == n
    assert again.evaluated == 2

    nxt = w.svc.evaluate_next_search(LOGIN, q, limit=50)
    assert nxt.stage == "deep" and nxt.tasks[0].candidates == 2
    w.run_all()
    got = w.svc.get_search(LOGIN, q, limit=50, offset=0)
    assert got.evaluated == 4 and len(_search_calls(w, deep=True)) == 2
    assert len(_search_calls(w, deep=False)) == 1


def test_search_stage_two_keeps_filters_of_the_run():
    w = _search_world(max_evaluated=2)
    f = SearchFilters(tags=("sql",))                       # EMP-1, EMP-4, EMP-6 - больше N=2
    w.svc.start_search(LOGIN, "аналитик банк", filters=f, limit=50)
    w.run_all()
    got = w.svc.get_search(LOGIN, "аналитик банк", filters=f, limit=50, offset=0)
    assert got.candidates_total == 3 and got.evaluated == 2
    keys = {i.employee_key for i in got.items}
    assert keys == {"EMP-1", "EMP-4", "EMP-6"}             # область поиска не расширилась
    assert _search_calls(w, deep=False)[0]["prompt"].count("### K") == 3


def test_search_external_screening_confirm_then_separate_deep_confirm():
    w = _search_world(max_evaluated=2, external_default=True)
    q = "аналитик sql rest"
    st = w.svc.start_search(LOGIN, q, limit=50)
    assert st.status == "awaiting_confirmation" and st.stage == "screening" and st.run_id
    assert w.llm_client.calls == []
    info = w.svc.run_info(LOGIN, st.run_id)
    assert info.kind == "search" and info.stage == "screening" and info.tasks[0].prompt
    w.svc.confirm_run(LOGIN, st.run_id)
    w.run_all()
    got = w.svc.get_search(LOGIN, q, limit=50, offset=0)
    assert got.status == "awaiting_confirmation" and got.stage == "deep"
    assert got.run_id != st.run_id and len(w.llm_client.calls) == 1
    w.svc.confirm_run(LOGIN, got.run_id)
    w.run_all()
    assert w.svc.get_search(LOGIN, q, limit=50, offset=0).evaluated == 2
