"""«Поиск по смыслу (LLM)» (FR-032, KON-38): детерминированный отбор, пакеты, кэш, stale,
внешний провайдер, деградация без LLM. Шлюз настоящий, клиент LLM и репозитории - подмены.
Сотрудники и тексты CV придуманы для теста."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.llm_gateway import AnonymizationError, LlmRunner
from app.llm_gateway.client import LlmCallError, LlmTimeoutError
from app.matching.factory import task_handler
from app.matching.semantic import (
    SKILL_WEIGHT,
    fallback_rationale,
    normalize_query,
    query_hash,
    query_terms,
    score_candidates,
    select_pool,
)
from app.matching.service import MatchingError, SearchFilters

from ._matching_fakes import World, reply_for

LOGIN = "tester"
QUERY = "Нужен аналитик с опытом интеграций REST и SQL в банковской сфере"


def _world(**kw) -> World:
    w = World(**kw)
    texts = [
        "Системный аналитик. Банковская сфера, интеграции REST, SQL, BPMN.",
        "Бизнес-аналитик. Страхование, BPMN, Visio.",
        "Системный аналитик. Интеграции REST, Kafka, банковский сектор.",
    ]
    for i, text in enumerate(texts, 1):
        fid = w.repo.add_employee(f"EMP-{i}", f"Сотрудник-{i:02d}", text=text)
        w.repo.skills[fid] = ["sql", "rest", "bpmn"] if i == 1 else (
            ["bpmn", "visio"] if i == 2 else ["rest", "kafka"])
        w.repo.states[fid] = dict(status="ok", task_id=None, model="m", by="x", code=None,
                                  msg=None, at=datetime(2026, 9, 1, tzinfo=UTC))
    return w


def _search_calls(w: World) -> list[dict]:
    return [c for c in w.llm_client.calls if c["prompt"].startswith("ЗАПРОС РУКОВОДИТЕЛЯ")]


def _get(w: World, q=QUERY, **kw):
    return w.svc.get_search(LOGIN, q, limit=kw.pop("limit", 50), offset=0, **kw)


# --- чистая логика ---

def test_query_normalization_hash_and_terms():
    assert normalize_query("  Нужен   SQL \n и REST ") == "нужен sql и rest"
    assert query_hash("Нужен SQL") == query_hash("  нужен   sql ")
    assert query_hash("sql") != query_hash("rest")
    assert len(query_hash("x")) == 64
    terms = query_terms("Нужен аналитик с опытом интеграций REST и SQL, REST")
    assert "аналитик" in terms and "rest" in terms and "sql" in terms
    assert "нужен" not in terms and terms.count("rest") == 1


def test_score_prefers_skills_then_text_and_pool_falls_back_to_all():
    cands = [
        ("A", "Анна", ["sql"], "ничего общего"),
        ("B", "Борис", ["kafka"], "работал с sql много лет"),
        ("C", "Вера", [], "совсем другое"),
    ]
    got = score_candidates(cands, "нужен sql")
    assert [s.key for s in got] == ["A", "B", "C"]
    assert got[0].score == SKILL_WEIGHT and got[0].skills == ("sql",)
    assert got[1].score == 1 and got[1].terms == ("sql",)
    assert [s.key for s in select_pool(got)] == ["A", "B"]
    nothing = score_candidates(cands, "виолончелист")
    assert [s.key for s in select_pool(nothing)] == ["A", "B", "C"]  # решает LLM
    assert fallback_rationale(nothing[0]).startswith("Совпадений")
    assert "sql" in fallback_rationale(got[0])


# --- сервис ---

def test_search_runs_llm_and_ranks_by_percent():
    w = _world()
    st = w.svc.start_search(LOGIN, QUERY)
    assert st.status == "running" and st.mode == "fallback" and st.notice
    assert [i.source for i in st.items] == ["fallback"] * 3 and st.evaluated == 0
    assert w.run_all() == 1
    got = _get(w)
    assert got.status == "done" and got.mode == "llm" and got.notice is None
    assert got.evaluated == 3 and got.total == 3 and got.model == "qwen-test"
    assert [i.percent for i in got.items] == [90, 89, 88]
    assert {i.source for i in got.items} == {"llm"}
    assert got.items[0].rationale.startswith("обоснование")
    assert got.candidates_total == 3 and got.calculated_at is not None and got.stale is False


def test_prompt_has_labels_without_names_and_keys():
    w = _world()
    w.repo.employees["EMP-1"]["note"] = "Не подключать к клиенту Х"
    w.svc.start_search(LOGIN, QUERY)
    w.run_all()
    [call] = _search_calls(w)
    p = call["prompt"]
    assert p.startswith("ЗАПРОС РУКОВОДИТЕЛЯ") and QUERY in p
    assert "### K1" in p and "### K3" in p
    assert "Сотрудник-0" not in p and "EMP-" not in p
    assert "Не подключать" in p  # заметка учитывается (FR-033)
    assert "JSON-массив" in call["system"]


def test_cache_by_query_cv_and_model_then_force_and_new_query():
    w = _world()
    w.svc.start_search(LOGIN, QUERY)
    w.run_all()
    assert len(_search_calls(w)) == 1
    st = w.svc.start_search(LOGIN, "  " + QUERY.upper() + " ")  # тот же запрос по смыслу
    assert st.status == "done" and w.run_all() == 0
    assert len(_search_calls(w)) == 1
    st = w.svc.start_search(LOGIN, QUERY, force=True)
    assert st.status == "running"
    w.run_all()
    assert len(_search_calls(w)) == 2
    w.svc.start_search(LOGIN, "аналитик по страхованию")
    w.run_all()
    assert len(_search_calls(w)) == 3


def test_results_shared_between_users_but_run_belongs_to_owner():
    w = _world()
    w.svc.start_search(LOGIN, QUERY)
    w.run_all()
    other = w.svc.start_search("second-user", QUERY)
    assert w.run_all() == 0 and other.mode == "llm"  # оценки из общего кэша
    assert other.requested_by is None and other.status == "none"


def test_cv_replacement_and_note_change_make_results_stale_or_recalculated():
    w = _world()
    w.svc.start_search(LOGIN, QUERY)
    w.run_all()
    w.repo.employees["EMP-2"]["note_updated_at"] = datetime(2026, 10, 2, 10, tzinfo=UTC)
    w.repo.employees["EMP-2"]["note"] = "Новая заметка"
    got = _get(w)
    assert got.stale is True and [i.stale for i in got.items].count(True) == 1
    w.repo.set_main_cv("EMP-3", "Новое резюме: аналитик, REST, банк")
    got = _get(w)
    sources = {i.employee_key: i.source for i in got.items}
    assert sources["EMP-3"] == "fallback" and sources["EMP-1"] == "llm"  # у нового файла оценки нет
    w.svc.start_search(LOGIN, QUERY)
    w.run_all()
    assert len(_search_calls(w)) == 2
    assert _get(w).evaluated == 3 and _get(w).stale is False


def test_filters_status_technology_and_text():
    w = _world()
    w.repo.employees["EMP-2"]["status"] = "Коммерческий проект"
    w.repo.employees["EMP-2"]["assignable"] = True   # флаг разошёлся со статусом: решает статус
    st = _get(w)
    assert {i.employee_key for i in st.items} == {"EMP-1", "EMP-3"}   # допустимые статусы
    st = _get(w, filters=SearchFilters(status="Коммерческий проект"))
    assert [i.employee_key for i in st.items] == ["EMP-2"]            # явный статус выигрывает
    st = _get(w, filters=SearchFilters(tags=("rest",)))
    assert {i.employee_key for i in st.items} == {"EMP-1", "EMP-3"}
    st = _get(w, filters=SearchFilters(tags=("rest", "kafka")))
    assert [i.employee_key for i in st.items] == ["EMP-3"]
    st = _get(w, filters=SearchFilters(tags=("rest", "kafka"), match_all=False))
    assert len(st.items) == 2
    assert _get(w, filters=SearchFilters(text="visio")).items == []  # EMP-2 недопустим по статусу
    st = _get(w, filters=SearchFilters(status="Коммерческий проект", text="visio"))
    assert [i.employee_key for i in st.items] == ["EMP-2"]


def test_search_larger_than_n_screens_all_then_deep_top_n():
    w = _world(max_evaluated=2)
    w.svc.start_search(LOGIN, QUERY)
    w.run_all()  # скрининг всех трёх по навыкам; по завершении ставится глубокая оценка топ-2
    calls = _search_calls(w)
    assert len(calls) == 2
    assert calls[0]["prompt"].count("### K") == 3 and "Текст CV:" not in calls[0]["prompt"]
    assert calls[1]["prompt"].count("### K") == 2 and "Текст CV:" in calls[1]["prompt"]
    got = _get(w)
    assert got.evaluated == 2 and got.total == 3  # третий - предварительная оценка скрининга
    assert got.items[-1].source == "screening" and got.items[-1].screen_percent is not None
    assert got.remaining == 1 and got.screened == 3


def test_batches_by_five_candidates():
    w = _world()
    for i in range(4, 9):
        w.repo.add_employee(f"EMP-{i}", f"Сотрудник-{i:02d}", text="Аналитик SQL REST банк")
    st = w.svc.start_search(LOGIN, QUERY)
    assert len(st.tasks) == 2 and sorted(t.candidates for t in st.tasks) == [3, 5]
    w.run_all()
    assert _get(w).status == "done" and _get(w).evaluated == 8


def test_no_provider_degrades_to_deterministic_ranking():
    w = _world()
    w.env.repo.providers.clear()
    st = w.svc.start_search(LOGIN, QUERY)
    assert st.status == "unavailable" and st.mode == "fallback"
    assert "недоступен" in st.notice and "технологиям и тексту" in st.notice
    assert w.llm_client.calls == []
    assert st.items[0].employee_key == "EMP-1" and st.items[0].matched_skills == ("rest", "sql")
    assert st.items[0].percent is None and "Совпало" in st.items[0].rationale


def test_explicit_unknown_provider_is_an_error_not_degradation():
    w = _world()
    from app.llm_gateway import GatewayError
    with pytest.raises(GatewayError):
        w.svc.start_search(LOGIN, QUERY, provider_id=9999)


@pytest.mark.parametrize("error", [
    LlmCallError("provider_http_error", "LLM вернула ошибку 401"),
    LlmTimeoutError(120),
])
def test_llm_error_or_timeout_degrades_with_message(error):
    w = _world()
    w.llm_client._error = error
    w.svc.start_search(LOGIN, QUERY)
    w.run_all()
    got = _get(w)
    assert got.status == "failed" and got.mode == "fallback" and got.evaluated == 0
    assert "не выполнено" in got.notice and "технологиям и тексту" in got.notice
    assert got.errors and len(got.items) == 3
    # повтор после починки: тот же запрос ставит новый прогон
    w.llm_client._error = None
    assert w.svc.start_search(LOGIN, QUERY).status == "running"
    w.run_all()
    assert _get(w).status == "done"


def test_unparsable_answer_degrades_and_does_not_leak_text():
    w = _world()
    w.match_reply = lambda p: "Извините, не могу"
    w.svc.start_search(LOGIN, QUERY)
    w.run_all()
    got = _get(w)
    assert got.status == "failed" and all("Извините" not in e for e in got.errors)
    assert got.items and all(i.source == "fallback" for i in got.items)


def test_partial_answer_keeps_unrated_as_fallback_below():
    w = _world()
    w.match_reply = lambda p: json.dumps([{"id": "K1", "percent": 70, "rationale": "ок"}])
    w.svc.start_search(LOGIN, QUERY)
    w.run_all()
    got = _get(w)
    assert got.status == "done" and got.evaluated == 1 and got.total == 3
    assert [i.source for i in got.items] == ["llm", "fallback", "fallback"]


def test_gateway_submit_failure_is_reported_not_raised(monkeypatch):
    w = _world(external_default=True)

    def boom(*a, **kw):
        raise AnonymizationError("не вышло")

    monkeypatch.setattr(w.env.gateway, "submit", boom)
    st = w.svc.start_search(LOGIN, QUERY)
    assert st.status == "failed" and st.tasks == [] and st.items
    assert st.errors == ["Не удалось надёжно обезличить текст для внешней LLM; отправка отменена"]
    assert st.notice and "технологиям и тексту" in st.notice
    assert w.llm_client.calls == []


def test_external_provider_needs_confirmation_before_any_call():
    w = _world(external_default=True)
    st = w.svc.start_search(LOGIN, QUERY)
    assert st.status == "awaiting_confirmation" and st.tasks[0].requires_confirmation
    assert "подтверждения" in st.notice and "технологиям и тексту" in st.notice
    assert w.llm_client.calls == [] and w.run_all() == 0
    assert len(st.items) == 3  # пока ранжирование без LLM
    w.env.gateway.confirm(LOGIN, st.tasks[0].task_id)
    w.run_all()
    assert len(_search_calls(w)) == 1 and _get(w).status == "done"


def test_second_apply_while_running_does_not_duplicate_tasks():
    w = _world()
    a = w.svc.start_search(LOGIN, QUERY)
    b = w.svc.start_search(LOGIN, QUERY, force=True)
    assert [t.task_id for t in a.tasks] == [t.task_id for t in b.tasks]
    assert w.run_all() == 1


def test_worker_handler_collects_search_without_api_call():
    w = _world()
    w.svc.start_search(LOGIN, QUERY)
    runner = LlmRunner(w.env.gateway, on_finished=task_handler(w.svc))
    while runner.run_once():
        pass
    assert w.repo.sbatches[0]["status"] == "done" and w.repo.sresults


def test_query_validation_and_retention_purge():
    w = _world()
    with pytest.raises(MatchingError) as ei:
        w.svc.start_search(LOGIN, "  ab ")
    assert ei.value.code == "bad_query"
    with pytest.raises(MatchingError):
        w.svc.get_search(LOGIN, "x" * 600, limit=10, offset=0)
    w.svc.start_search(LOGIN, QUERY)
    assert w.repo.purged == 30


def test_get_without_run_makes_no_llm_calls_and_matches_reply_helper():
    w = _world()
    got = _get(w)
    assert got.status == "none" and got.mode == "fallback" and "не запускалось" in got.notice
    assert w.llm_client.calls == [] and reply_for  # импорт использован: общая подмена ответов
