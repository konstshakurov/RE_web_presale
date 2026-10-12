"""matching на настоящих БД (KON-38): отбор кандидатов по статусу, навыки привязаны к файлу,
кэш рекомендаций, прогоны и пакеты, фильтры «Резюме», сквозной сценарий с кэшем.

Нужны TEST_CV_DB_URL (cv_rw, миграции cv_db V001-V007) и TEST_APP_DB_URL (app_rw, app_db
V001-V008); без них пропуск. Данные тестов: сотрудники KONT38-*, потребность KONT38-1; после
теста удаляются. LLM не вызывается: шлюз с клиентом-подменой.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from app.app_repo import create_pool
from app.cv.repo import CvRepo
from app.matching.repo import MatchRepo, NeedSource, NewBatch, NewRec, RunRow
from app.matching.service import MatchingService

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
        c.execute("DELETE FROM cv.recommendation WHERE need_key LIKE 'KONT38-%'")
        c.execute("DELETE FROM cv.employee_file WHERE employee_key LIKE 'KONT38-%' "
                  "AND (is_archived OR kind <> 'main_cv')")
        c.execute("UPDATE cv.employee_file SET is_archived = true, archived_at = now() "
                  "WHERE employee_key LIKE 'KONT38-%' AND kind = 'main_cv' AND NOT is_archived")
        c.execute("DELETE FROM cv.employee_file WHERE employee_key LIKE 'KONT38-%'")
        c.execute("DELETE FROM cv.employee WHERE jira_key LIKE 'KONT38-%'")
    with app_pool.connection() as c:
        c.execute("DELETE FROM app.jira_need WHERE jira_key LIKE 'KONT38-%'")


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
