"""matching на настоящих БД (KON-38): отбор кандидатов по статусу, навыки привязаны к файлу,
кэш рекомендаций, прогоны и пакеты, фильтры «Резюме», сквозной сценарий с кэшем.

Нужны TEST_CV_DB_URL (cv_rw, миграции cv_db V001-V009) и TEST_APP_DB_URL (app_rw, app_db
V001-V009); без них пропуск. Данные тестов: сотрудники KONT38-*, потребность KONT38-1; после
теста удаляются. LLM не вызывается: шлюз с клиентом-подменой.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from app.app_repo import create_pool
from app.cv.repo import CvRepo
from app.matching.repo import (
    MatchRepo,
    NeedSource,
    NewBatch,
    NewRec,
    NewScreen,
    RunRow,
    SearchRunRow,
)
from app.matching.service import MatchingService
from app.matching.settings import AppSettings

from ._llm_fakes import Env, FakeClient
from ._matching_fakes import reply_for, skills_reply

CV_DSN = os.environ.get("TEST_CV_DB_URL")
APP_DSN = os.environ.get("TEST_APP_DB_URL")
pytestmark = pytest.mark.skipif(
    not (CV_DSN and APP_DSN), reason="TEST_CV_DB_URL / TEST_APP_DB_URL не заданы")

NEED = "KONT38-1"
LOGIN = "kont38-user"
T1 = datetime(2026, 10, 10, 9, tzinfo=UTC)
T2 = datetime(2026, 10, 11, 9, tzinfo=UTC)

# (ключ, статус, допустим к назначению)
EMPLOYEES = [
    ("KONT38-10", "Свободен", True),
    ("KONT38-11", "Ожидает подключения", True),
    ("KONT38-12", "Коммерческий проект", False),
    ("KONT38-13", "Уволен", False),
    ("KONT38-14", "Свободен", True),   # без CV
    ("KONT38-15", "Свободен", True),   # CV без извлечённого текста
]


def _purge(cv_pool, app_pool):
    with cv_pool.connection() as c:
        c.execute("DELETE FROM cv.match_run WHERE need_key LIKE 'KONT38-%'")
        c.execute("DELETE FROM cv.match_screen WHERE need_key LIKE 'KONT38-%'")
        c.execute("DELETE FROM cv.search_run WHERE requested_by LIKE 'kont38-%'")
        c.execute("DELETE FROM cv.recommendation WHERE need_key LIKE 'KONT38-%'")
        c.execute("DELETE FROM cv.employee_file WHERE employee_key LIKE 'KONT38-%' "
                  "AND (is_archived OR kind <> 'main_cv')")
        c.execute("UPDATE cv.employee_file SET is_archived = true, archived_at = now() "
                  "WHERE employee_key LIKE 'KONT38-%' AND kind = 'main_cv' AND NOT is_archived")
        c.execute("DELETE FROM cv.employee_file WHERE employee_key LIKE 'KONT38-%'")
        c.execute("DELETE FROM cv.employee WHERE jira_key LIKE 'KONT38-%'")
    with app_pool.connection() as c:
        c.execute("DELETE FROM app.jira_need WHERE jira_key LIKE 'KONT38-%'")
        c.execute("DELETE FROM app.app_setting WHERE key IN ('match_deep_size', "
                  "'match_screen_batch')")


@pytest.fixture()
def env():
    cv_pool, app_pool = create_pool(CV_DSN), create_pool(APP_DSN)
    cv_pool.open(wait=True)
    app_pool.open(wait=True)
    _purge(cv_pool, app_pool)
    with cv_pool.connection() as c:
        for key, status, ok in EMPLOYEES:
            c.execute(
                "INSERT INTO cv.employee (jira_key, full_name, status, assignable) "
                "VALUES (%s, %s, %s, %s)", (key, f"Сотрудник {key[-2:]}", status, ok))
    with app_pool.connection() as c:
        c.execute(
            "INSERT INTO app.jira_need (jira_key, title, description, requirements_text, status, "
            "jira_created_at, jira_updated_at) VALUES (%s, 'Нужен аналитик SQL', 'REST', "
            "'BPMN, опыт 3 года', 'Новая', %s, %s)", (NEED, T1, T1))
    cv = CvRepo(cv_pool)
    files = {}
    for key, _, _ in EMPLOYEES:
        if key == "KONT38-14":
            continue
        ok = key != "KONT38-15"
        files[key] = cv.add_file(
            employee_key=key, kind="main_cv", file_name=f"cv-{key[-2:]}.docx",
            mime_type="application/octet-stream", content=b"x" * 10, uploaded_by="u",
            extracted_text=f"Текст резюме {key[-2:]} аналитик" if ok else None,
            extraction_status="ok" if ok else "error")
    yield cv_pool, app_pool, MatchRepo(cv_pool), cv, files
    _purge(cv_pool, app_pool)
    cv_pool.close()
    app_pool.close()


def test_candidates_exclude_unassignable_no_cv_and_no_text(env):
    _, _, repo, _, files = env
    keys = [c.employee_key for c in repo.list_candidates() if c.employee_key.startswith("KONT38-")]
    assert keys == ["KONT38-10", "KONT38-11"]
    all_keys = [c.employee_key for c in repo.list_candidates(only_assignable=False)
                if c.employee_key.startswith("KONT38-")]
    assert all_keys == ["KONT38-10", "KONT38-11", "KONT38-12", "KONT38-13"]
    assert repo.get_main_cv("KONT38-14") is None and repo.get_main_cv("KONT38-15") is None
    assert repo.get_main_cv("KONT38-10")[0] == files["KONT38-10"]


def test_need_source(env):
    _, app_pool, *_ = env
    n = NeedSource(app_pool).get_need(NEED)
    assert n.title == "Нужен аналитик SQL" and n.requirements_text == "BPMN, опыт 3 года"
    assert n.jira_updated_at == T1 and n.tags == ()
    assert NeedSource(app_pool).get_need("KONT38-999") is None


def test_skills_state_bound_to_file_and_replaced_with_file(env):
    cv_pool, _, repo, cv, files = env
    fid = files["KONT38-10"]
    assert repo.claim_skills(fid, LOGIN, "m") is True
    assert repo.claim_skills(fid, LOGIN, "m") is False           # идёт: второй раз не занять
    repo.set_skills_task(fid, "11111111-1111-4111-8111-111111111111")
    assert repo.state_by_task("11111111-1111-4111-8111-111111111111").file_id == fid
    assert [s.file_id for s in repo.pending_skill_states() if s.file_id == fid] == [fid]
    assert repo.finish_skills_ok(fid, ["sql", "bpmn"]) is True
    assert repo.finish_skills_ok(fid, ["x"]) is False             # повтор не меняет теги
    assert repo.get_skills(fid) == ("bpmn", "sql")
    assert repo.claim_skills(fid, LOGIN, "m") is False            # извлечённое не пересчитывается
    # замена файла: новый file_id, прежние навыки не наследуются, извлечение возможно
    new = cv.add_file(employee_key="KONT38-10", kind="main_cv", file_name="n.docx",
                      mime_type="x", content=b"y" * 5, uploaded_by="u",
                      extracted_text="Новый текст", extraction_status="ok")
    assert repo.get_skills(new) == () and repo.get_skill_state(new) is None
    assert repo.claim_skills(new, LOGIN, "m") is True
    assert repo.finish_skills_failed(new, "timeout", "x" * 900) is True
    assert repo.get_skill_state(new).status == "failed"
    assert repo.claim_skills(new, LOGIN, "m") is True             # после сбоя можно повторить
    # архивная версия остаётся со своими навыками
    assert repo.get_skills(fid) == ("bpmn", "sql")


def test_recommendation_cache_upsert_and_file_check(env):
    _, _, repo, cv, files = env
    f10, f11 = files["KONT38-10"], files["KONT38-11"]
    recs = [NewRec("KONT38-10", f10, 80, "хорошо", None), NewRec("KONT38-11", f11, 40, "мало", T1),
            NewRec("KONT38-10", f11, 99, "чужой файл", None)]  # файл другого сотрудника
    assert repo.upsert_recommendations(NEED, recs, model="m1", need_updated_at=T1) == 2
    assert repo.upsert_recommendations(
        NEED, [NewRec("KONT38-10", f10, 55, "пересчёт", None)], model="m1", need_updated_at=T2) == 1
    got = {r.employee_key: r for r in repo.list_recommendations(NEED)}
    assert len(got) == 2 and got["KONT38-10"].match_percent == 55
    assert got["KONT38-10"].need_jira_updated_at == T2
    assert got["KONT38-11"].note_snapshot_at == T1
    # другая модель - отдельная запись кэша
    repo.upsert_recommendations(NEED, [NewRec("KONT38-10", f10, 70, "m2", None)], model="m2",
                                need_updated_at=T2)
    assert len(repo.list_recommendations(NEED)) == 3
    # замена CV: кандидатом становится новая версия, рекомендация относится к прежней
    new = cv.add_file(employee_key="KONT38-10", kind="main_cv", file_name="n.docx", mime_type="x",
                      content=b"y", uploaded_by="u", extracted_text="t", extraction_status="ok")
    cur = {c.employee_key: c.file_id for c in repo.list_candidates()}
    assert cur["KONT38-10"] == new and f10 != new


def test_runs_and_batches(env):
    cv_pool, _, repo, _, files = env
    t1, t2 = "22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333"
    run = RunRow(NEED, LOGIN, 1, "m", T1, T1)
    with repo.run_tx(NEED) as tx:
        assert tx.run() is None and tx.batches() == []
        tx.replace(run, [NewBatch(t1, ("KONT38-10", "KONT38-11"),
                                  (files["KONT38-10"], files["KONT38-11"]))])
    assert repo.get_run(NEED).model == "m"
    b = repo.batch_by_task(t1)
    assert b.employee_keys == ("KONT38-10", "KONT38-11") and b.status == "pending"
    assert [x.id for x in repo.pending_batches() if x.need_key == NEED] == [b.id]
    assert repo.finish_batch(b.id, "done") is True
    assert repo.finish_batch(b.id, "failed", "x", "y") is False   # итог один раз
    # новый прогон заменяет прежний вместе с пакетами
    with repo.run_tx(NEED) as tx:
        tx.replace(run, [NewBatch(t2, ("KONT38-10",), (files["KONT38-10"],))])
    assert [x.task_id for x in repo.list_batches(NEED)] == [t2]
    assert repo.batch_by_task(t1) is None


def test_resume_filters(env):
    _, _, repo, _, files = env
    f10, f11 = files["KONT38-10"], files["KONT38-11"]
    repo.claim_skills(f10, LOGIN, "m")
    repo.set_skills_task(f10, "44444444-4444-4444-8444-444444444444")
    repo.finish_skills_ok(f10, ["sql", "bpmn", "kafka"])
    repo.claim_skills(f11, LOGIN, "m")
    repo.finish_skills_ok(f11, ["sql"])

    def keys(**kw):
        base = dict(query=None, status=None, tags=[], match_all=True, limit=100, offset=0)
        base.update(kw)
        rows, total = repo.list_resume(**base)
        return [r.employee_key for r in rows if r.employee_key.startswith("KONT38-")], total

    assert keys(tags=["sql"])[0] == ["KONT38-10", "KONT38-11"]
    assert keys(tags=["sql", "kafka"])[0] == ["KONT38-10"]
    assert keys(tags=["kafka", "nosuch"], match_all=False)[0] == ["KONT38-10"]
    assert keys(tags=["sql", "nosuch"])[0] == []
    assert keys(status="Ожидает подключения")[0] == ["KONT38-11"]
    assert keys(query="резюме 11")[0] == ["KONT38-11"]        # по тексту CV
    assert keys(query="cv-10.docx")[0] == ["KONT38-10"]       # по имени файла
    assert keys(query="kont38-12")[0] == ["KONT38-12"]        # по ключу
    assert keys(query="100%")[0] == []                        # спецсимволы экранированы
    rows, _ = repo.list_resume(query="KONT38-10", status=None, tags=[], match_all=True,
                               limit=5, offset=0)
    assert rows[0].skills == ("bpmn", "kafka", "sql") and rows[0].skills_status == "ok"
    techs = dict(repo.list_technologies())
    assert techs.get("kafka") == 1 and techs.get("sql", 0) >= 2


def test_end_to_end_with_real_repos_cache_and_status_filter(env):
    _, app_pool, repo, _, files = env
    client = FakeClient(lambda p: reply_for(p) if p.startswith("ПОТРЕБНОСТЬ") else skills_reply(p))
    e = Env(client=client)
    svc = MatchingService(repo, NeedSource(app_pool), e.gateway)

    assert svc.schedule_skills("KONT38-10", LOGIN).outcome == "scheduled"
    st = svc.start_match(NEED, LOGIN)
    assert st.status == "running"
    while (t := e.gateway.run_next()) is not None:
        svc.collect_task(t.id)
    assert repo.get_skills(files["KONT38-10"]) == ("bpmn", "rest api", "sql")

    st = svc.get_state(NEED, limit=10, offset=0)
    got = [i.employee_key for i in st.items if i.employee_key.startswith("KONT38-")]
    assert st.status == "done" and sorted(got) == ["KONT38-10", "KONT38-11"]
    assert all(0 <= i.percent <= 100 and i.rationale for i in st.items)
    n_calls = len(client.calls)

    st2 = svc.start_match(NEED, LOGIN)             # кэш: LLM не вызывается
    assert e.gateway.run_next() is None and len(client.calls) == n_calls and st2.status == "done"

    # сотрудник ушёл в недопустимый статус: из выдачи пропадает без пересчёта
    with env[0].connection() as c:
        c.execute("UPDATE cv.employee SET status = 'Коммерческий проект', assignable = false "
                  "WHERE jira_key = 'KONT38-11'")
    st3 = svc.get_state(NEED, limit=10, offset=0)
    assert "KONT38-11" not in [i.employee_key for i in st3.items]
    assert len(client.calls) == n_calls


def test_search_run_batches_results_cache_and_purge(env):
    cv_pool, _, repo, cv, files = env
    h = "a" * 64
    t1, t2 = "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666"
    f10, f11 = files["KONT38-10"], files["KONT38-11"]
    run = SearchRunRow(LOGIN, h, "аналитик с SQL", 1, "m", T1)
    with repo.search_tx(LOGIN, h) as tx:
        assert tx.batches() == []
        tx.replace(run, [NewBatch(t1, ("KONT38-10", "KONT38-11"), (f10, f11))])
    assert repo.get_search_run(LOGIN, h).query_text == "аналитик с SQL"
    assert repo.get_search_run("kont38-other", h) is None
    b = repo.search_batch_by_task(t1)
    assert b.employee_keys == ("KONT38-10", "KONT38-11") and b.status == "pending"
    assert repo.finish_search_batch(b.id, "done") is True
    assert repo.finish_search_batch(b.id, "failed", "x", "y") is False
    with repo.search_tx(LOGIN, h) as tx:  # новый прогон заменяет прежний вместе с пакетами
        tx.replace(run, [NewBatch(t2, ("KONT38-10",), (f10,))])
    assert [x.task_id for x in repo.list_search_batches(LOGIN, h)] == [t2]
    assert repo.search_batch_by_task(t1) is None

    recs = [NewRec("KONT38-10", f10, 80, "хорошо", None), NewRec("KONT38-11", f11, 30, "мало", T1),
            NewRec("KONT38-10", f11, 99, "чужой файл", None)]
    assert repo.upsert_search_results(h, recs, model="m1") == 2
    assert repo.upsert_search_results(
        h, [NewRec("KONT38-10", f10, 55, "пересчёт", None)], model="m1") == 1
    got = {r.employee_key: r for r in repo.list_search_results(h)}
    assert len(got) == 2 and got["KONT38-10"].match_percent == 55
    assert got["KONT38-11"].note_snapshot_at == T1
    repo.upsert_search_results(h, [NewRec("KONT38-10", f10, 70, "m2", None)], model="m2")
    assert len(repo.list_search_results(h)) == 3            # другая модель - отдельная запись
    assert repo.list_search_results("b" * 64) == []         # другой запрос - другой кэш

    # замена файла: оценки по прежнему CV уходят вместе с ним (FK ON DELETE CASCADE)
    cv.add_file(employee_key="KONT38-11", kind="main_cv", file_name="n.docx", mime_type="x",
                content=b"y", uploaded_by="u", extracted_text="t", extraction_status="ok")
    # срок хранения: старый прогон и оценки удаляются, свежие остаются
    with cv_pool.connection() as c:
        c.execute("UPDATE cv.search_run SET created_at = now() - interval '40 days' "
                  "WHERE query_hash = %s", (h,))
        c.execute("UPDATE cv.search_result SET created_at = now() - interval '40 days' "
                  "WHERE query_hash = %s AND model = 'm2'", (h,))
    repo.purge_search(30)
    assert repo.get_search_run(LOGIN, h) is None and repo.list_search_batches(LOGIN, h) == []
    assert {r.model for r in repo.list_search_results(h)} == {"m1"}


def test_semantic_search_end_to_end_on_real_repos(env):
    _, app_pool, repo, _, files = env
    client = FakeClient(lambda p: reply_for(p) if p.startswith("ЗАПРОС") else skills_reply(p))
    e = Env(client=client)
    svc = MatchingService(repo, NeedSource(app_pool), e.gateway)
    query = "аналитик резюме"

    st = svc.start_search(LOGIN, query, limit=50)
    assert st.status == "running" and st.mode == "fallback"
    while (t := e.gateway.run_next()) is not None:
        svc.collect_task(t.id)
    st = svc.get_search(LOGIN, query, limit=50, offset=0)
    got = [i for i in st.items if i.employee_key.startswith("KONT38-")]
    assert st.status == "done" and st.mode == "llm"
    assert sorted(i.employee_key for i in got) == ["KONT38-10", "KONT38-11"]
    assert all(i.source == "llm" and 0 <= i.percent <= 100 for i in got)
    n = len(client.calls)

    again = svc.start_search(LOGIN, "  АНАЛИТИК   резюме ", limit=50)  # кэш по запросу+CV+модели
    assert again.status == "done" and e.gateway.run_next() is None and len(client.calls) == n
    other = svc.get_search("kont38-other", query, limit=50, offset=0)    # чужой прогон не виден
    assert other.status == "none" and other.mode == "llm"

    with env[0].connection() as c:
        c.execute("UPDATE cv.employee SET note = 'не подключать', note_author = 'ro', "
                  "note_updated_at = now() WHERE jira_key = 'KONT38-11'")
    st = svc.get_search(LOGIN, query, limit=50, offset=0)
    assert st.stale is True                                              # заметка изменилась
    assert len(client.calls) == n


# --- двухступенчатая оценка (cv_db V009) ---

def test_screen_cache_run_stage_and_group_lookup(env):
    cv_pool, app_pool, repo, cv, files = env
    f10, f11 = files["KONT38-10"], files["KONT38-11"]
    t1, t2 = "77777777-7777-4777-8777-777777777777", "88888888-8888-4888-8888-888888888888"
    run1, run2 = "99999999-9999-4999-8999-999999999991", "99999999-9999-4999-8999-999999999992"
    with repo.run_tx(NEED) as tx:
        tx.replace(RunRow(NEED, LOGIN, 1, "m", T1, T1, "pending"),
                   [NewBatch(t1, ("KONT38-10",), (f10,), "screen", run1)])
    assert repo.get_run(NEED).chain_state == "pending"
    b = repo.batch_by_task(t1)
    assert (b.stage, b.run_id) == ("screen", run1)
    assert [x.task_id for x in repo.batches_by_run(run1)] == [t1]
    assert repo.batches_by_run(run2) == []
    with repo.run_tx(NEED) as tx:
        tx.finish_chain()
        assert tx.run().chain_state == "done"
    with repo.run_tx(NEED) as tx:       # ступень 2 - новый прогон со своим run_id
        tx.replace(RunRow(NEED, LOGIN, 1, "m", T1, T1, "done"),
                   [NewBatch(t2, ("KONT38-11",), (f11,), "deep", run2)])
    assert repo.get_run(NEED).chain_state == "done" and repo.batches_by_run(run1) == []
    assert repo.batch_by_task(t2).stage == "deep"

    rows = [NewScreen("KONT38-10", f10, 80, "a" * 64), NewScreen("KONT38-11", f11, 20, "b" * 64),
            NewScreen("KONT38-10", f11, 99, "c" * 64)]            # чужой файл пропускается
    assert repo.upsert_match_screens(NEED, rows, model="m1", need_updated_at=T1) == 2
    assert repo.upsert_match_screens(
        NEED, [NewScreen("KONT38-10", f10, 55, "d" * 64)], model="m1", need_updated_at=T2) == 1
    got = {r.employee_key: r for r in repo.list_match_screens(NEED)}
    assert got["KONT38-10"].match_percent == 55 and got["KONT38-10"].skills_hash == "d" * 64
    assert got["KONT38-10"].need_jira_updated_at == T2 and len(got) == 2
    repo.upsert_match_screens(NEED, [NewScreen("KONT38-10", f10, 70, "d" * 64)],
                              model="m2", need_updated_at=T2)
    assert len(repo.list_match_screens(NEED)) == 3                 # другая модель - своя запись

    h = "e" * 64
    t3 = "88888888-8888-4888-8888-888888888883"
    run3 = "99999999-9999-4999-8999-999999999993"
    scope = {"status": None, "tags": ["sql"], "match_all": True, "text": None}
    with repo.search_tx(LOGIN, h) as tx:
        tx.replace(SearchRunRow(LOGIN, h, "аналитик sql", 1, "m", T1, "pending_force", scope),
                   [NewBatch(t3, ("KONT38-10",), (f10,), "screen", run3)])
        assert tx.run().chain_state == "pending_force"
    sr = repo.get_search_run(LOGIN, h)
    assert sr.scope == scope and sr.chain_state == "pending_force"
    assert [x.run_id for x in repo.search_batches_by_run(run3)] == [run3]
    assert repo.upsert_search_screens(h, rows[:2], model="m1") == 2
    assert {r.employee_key for r in repo.list_search_screens(h)} == {"KONT38-10", "KONT38-11"}
    assert repo.get_skills_many([f10, f11]) == {}                  # навыков ещё нет

    # срок хранения чистит и скрининг поиска
    with cv_pool.connection() as c:
        c.execute("UPDATE cv.search_screen SET created_at = now() - interval '40 days' "
                  "WHERE query_hash = %s", (h,))
    repo.purge_search(30)
    assert repo.list_search_screens(h) == []


def test_two_stage_end_to_end_on_real_repos_and_app_settings(env):
    cv_pool, app_pool, repo, _, files = env
    settings = AppSettings(app_pool)
    assert settings.deep_size() == 30 and settings.screen_batch() == 50     # по умолчанию
    settings.set(deep_size=5, screen_batch=10)
    assert (settings.deep_size(), settings.screen_batch()) == (5, 10)
    with pytest.raises(ValueError):
        settings.set(deep_size=4)
    with app_pool.connection() as c:        # значение вне границ в самой БД - по умолчанию
        c.execute("UPDATE app.app_setting SET value = '1000' WHERE key = 'match_deep_size'")
    assert settings.deep_size() == 30
    settings.set(deep_size=5, screen_batch=10)

    # 10 допустимых с CV (2 из env + 8 новых), N=5 -> скрининг, затем глубокая оценка топ-5
    with cv_pool.connection() as c:
        for i in range(20, 28):
            c.execute("INSERT INTO cv.employee (jira_key, full_name, status, assignable) "
                      "VALUES (%s, %s, 'Свободен', true)", (f"KONT38-{i}", f"Сотрудник {i}"))
    cv = CvRepo(cv_pool)
    for i in range(20, 28):
        fid = cv.add_file(
            employee_key=f"KONT38-{i}", kind="main_cv", file_name=f"cv-{i}.docx",
            mime_type="x", content=b"x", uploaded_by="u", extracted_text=f"Резюме {i} аналитик",
            extraction_status="ok")
        repo.claim_skills(fid, LOGIN, "m")
        repo.finish_skills_ok(fid, ["sql"] if i % 2 else ["java"])

    client = FakeClient(lambda p: reply_for(p, percent=lambda n: 100 - 7 * n)
                        if p.startswith("ПОТРЕБНОСТЬ") else skills_reply(p))
    e = Env(client=client)
    svc = MatchingService(repo, NeedSource(app_pool), e.gateway, settings=settings)
    st = svc.start_match(NEED, LOGIN)
    assert st.stage == "screening"
    while (t := e.gateway.run_next()) is not None:
        svc.collect_task(t.id)
    st = svc.get_state(NEED, limit=50, offset=0)
    assert st.stage == "deep" and st.status == "done" and st.evaluated == 5
    assert st.screened == 8 and st.screen_total == 8 and st.candidates_total == 10
    assert st.remaining == 5
    n_calls = len(client.calls)
    svc.start_match(NEED, LOGIN)                                 # кэш скрининга и глубокой оценки
    assert e.gateway.run_next() is None and len(client.calls) == n_calls

    st = svc.evaluate_next(NEED, LOGIN)
    assert st.stage == "deep" and st.tasks[0].candidates >= 1
    while (t := e.gateway.run_next()) is not None:
        svc.collect_task(t.id)
    assert svc.get_state(NEED, limit=50, offset=0).evaluated == 10
    with app_pool.connection() as c:
        c.execute("DELETE FROM app.app_setting WHERE key IN ('match_deep_size', "
                  "'match_screen_batch')")
