"""API matching (KON-38): права и CSRF, сопоставление, профиль без LLM, фильтры «Резюме»,
постановка навыков при загрузке основного CV. БД не нужна: репозитории в памяти."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.matching.router import router as matching_router

from ._cv_fakes import build_docx, make_client
from ._matching_fakes import World
from .test_cv_api import EMP, FakeCvRepo, FakeLimits, upload

NEED = "ANALYTICS-1"
CV_TEXT = "Профиль\nСистемный аналитик, опыт работы 5 лет\nОсновной стек\nSQL, BPMN\n"


def _client(world: World, cv: FakeCvRepo | None = None):
    cv = cv or FakeCvRepo()
    client, headers, app = make_client(cv, FakeLimits())
    app.include_router(matching_router)
    app.state.matching = world.svc
    return client, headers, app, cv


def _world() -> World:
    w = World()
    w.needs.add(NEED)
    w.repo.add_employee("EMP-1", "Сотрудник-01", text=CV_TEXT, extra="Подробности опыта")
    w.repo.add_employee("EMP-2", "Сотрудник-02", text="Другое CV")
    return w


def test_requires_login_and_csrf():
    w = _world()
    app = FastAPI()
    app.include_router(matching_router)
    app.state.matching = w.svc
    c = TestClient(app)
    assert c.get(f"/needs/{NEED}/match").status_code == 401
    assert c.post(f"/needs/{NEED}/match").status_code == 401
    assert c.get("/resume/employees").status_code == 401
    client, headers, _, _ = _client(w)
    assert client.post(f"/needs/{NEED}/match").status_code == 403   # без CSRF
    assert client.post(f"/needs/{NEED}/match", headers=headers).status_code == 202


def test_match_flow_over_http():
    w = _world()
    client, headers, _, _ = _client(w)
    r = client.post(f"/needs/{NEED}/match", headers=headers, json={})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "running" and body["items"] == [] and len(body["tasks"]) == 1
    assert body["tasks"][0]["requires_confirmation"] is False

    w.run_all()
    r = client.get(f"/needs/{NEED}/match", params={"limit": 1})
    got = r.json()
    assert got["status"] == "done" and got["total"] == 2 and len(got["items"]) == 1
    item = got["items"][0]
    assert item["percent"] == 90 and item["rationale"] and item["full_name"] == "Сотрудник-01"
    assert item["gaps"] == ["нет опыта Kafka (K1)", "нет домена банки"]
    assert got["calculated_at"] and got["stale"] is False
    more = client.get(f"/needs/{NEED}/match", params={"limit": 10, "offset": 1}).json()
    assert [i["employee_key"] for i in more["items"]] == ["EMP-2"]


def test_match_counts_for_needs_list():
    w = _world()
    w.needs.add("ANALYTICS-2")
    client, headers, _, _ = _client(w)
    empty = client.get("/needs/match-counts", params={"keys": [NEED, "ANALYTICS-2"]})
    assert empty.status_code == 200 and empty.json() == {"counts": {}}
    client.post(f"/needs/{NEED}/match", headers=headers, json={})
    w.run_all()
    got = client.get("/needs/match-counts", params={"keys": [NEED, "ANALYTICS-2", "bad_key"]})
    assert got.json() == {"counts": {NEED: 2}}


def test_match_errors_and_validation():
    w = _world()
    client, headers, _, _ = _client(w)
    assert client.post("/needs/ANALYTICS-404/match", headers=headers).status_code == 404
    assert client.get("/needs/ANALYTICS-404/match").status_code == 404
    assert client.get("/needs/bad_key/match").status_code == 422
    assert client.get(f"/needs/{NEED}/match", params={"limit": 0}).status_code == 422
    r = client.post(f"/needs/{NEED}/match", headers=headers, json={"provider_id": 9999})
    assert r.status_code == 404


def test_no_cv_store_is_503():
    client, headers, app2 = make_client(FakeCvRepo(), FakeLimits())
    app2.include_router(matching_router)
    app2.state.matching = None
    assert client.get(f"/needs/{NEED}/match").status_code == 503
    assert client.get("/resume/employees").status_code == 503


def test_resume_filters_and_technologies():
    w = _world()
    client, headers, _, _ = _client(w)
    w.svc.schedule_skills("EMP-1", "u")
    w.svc.schedule_skills("EMP-2", "u")
    w.run_all()
    w.repo.skills[w.repo.get_main_cv("EMP-2")[0]] = ["java"]
    r = client.get("/resume/employees", params=[("technology", "SQL"), ("technology", " bpmn ")])
    assert r.status_code == 200 and r.json()["total"] == 1
    row = r.json()["items"][0]
    assert row["employee_key"] == "EMP-1" and row["description"] == "Подробности опыта"
    assert row["skills"] == ["bpmn", "rest api", "sql"] and row["skills_status"] == "ok"
    any_ = client.get("/resume/employees", params=[("technology", "java"), ("technology", "sql"),
                                                   ("match", "any")]).json()
    assert any_["total"] == 2
    assert client.get("/resume/employees", params={"match": "x"}).status_code == 422
    techs = {t["tag"]: t["employees"] for t in client.get("/resume/technologies").json()}
    assert techs["sql"] == 1 and techs["java"] == 1


def test_extract_skills_endpoint_and_unknown_employee():
    w = _world()
    cv = FakeCvRepo()
    client, headers, _, _ = _client(w, cv)
    r = client.post("/resume/employees/EMPT-1/skills", headers=headers)
    assert r.status_code == 202 and r.json()["outcome"] == "no_cv"
    assert client.post("/resume/employees/NOPE-9/skills", headers=headers).status_code == 404
    r = client.post("/resume/skills/backfill", headers=headers)
    assert r.status_code == 202 and r.json()["scheduled"] == 2


def test_profile_without_llm():
    w = _world()
    cv = FakeCvRepo()
    client, headers, app, _ = _client(w, cv)
    # карточка - в репозитории matching, файлы - в хранилище cv (подмены раздельные)
    w.repo.employees[EMP] = dict(
        name="Тестовый", status="Свободен", assignable=True, note="заметка",
        note_author="РО", note_updated_at=None, extra="Описание")
    app.state.cv = cv
    assert upload(client, headers, body=build_docx(
        ["Профиль", "Системный аналитик, опыт работы 5 лет", "Основной стек", "SQL, BPMN"]
    )).status_code == 201
    assert upload(client, headers, name="extra.docx", kind="extra_cv").status_code == 201
    app.state.matching = w.svc  # в загрузке matching - реальный сервис: LLM-задача навыков
    calls = len(w.llm_client.calls)
    r = client.get(f"/resume/employees/{EMP}/profile")
    assert r.status_code == 200
    p = r.json()
    assert p["full_name"] == "Тестовый" and p["description"] == "Описание"
    assert p["note"] == "заметка" and p["main_cv"]["file_name"] == "cv.docx"
    assert [f["kind"] for f in p["files"]] == ["main_cv", "extra_cv"]
    assert p["sections"]["stack"] == "SQL, BPMN"
    assert "Системный аналитик" in p["sections"]["profile"]
    assert p["experience_years"] == 5
    assert len(w.llm_client.calls) == calls            # профиль не обращается к LLM (FR-003)
    assert client.get("/resume/employees/NOPE-9/profile").status_code == 404


def test_upload_main_cv_schedules_skills_and_failure_does_not_break_upload():
    w = _world()
    cv = FakeCvRepo()
    client, headers, app, _ = _client(w, cv)
    scheduled: list[tuple[str, str]] = []

    class Spy:
        def schedule_skills(self, key, login):
            scheduled.append((key, login))

    app.state.matching = Spy()
    assert upload(client, headers).status_code == 201
    assert scheduled == [(EMP, "ivan")]
    # дополнительное CV и материалы навыков не ставят
    assert upload(client, headers, name="x.docx", kind="extra_cv").status_code == 201
    assert upload(client, headers, name="m.txt", kind="materials", body=b"text").status_code == 201
    assert len(scheduled) == 1

    class Broken:
        def schedule_skills(self, key, login):
            raise RuntimeError("шлюз недоступен")

    app.state.matching = Broken()
    assert upload(client, headers).status_code == 201   # загрузка не откатывается


# --- «Поиск по смыслу (LLM)» (FR-032) ---

SQ = "Нужен аналитик с SQL и интеграциями REST"


def test_semantic_search_requires_login_and_csrf_and_validates_query():
    w = _world()
    app = FastAPI()
    app.include_router(matching_router)
    app.state.matching = w.svc
    c = TestClient(app)
    assert c.get("/resume/semantic-search", params={"query": SQ}).status_code == 401
    assert c.post("/resume/semantic-search", json={"query": SQ}).status_code == 401
    client, headers, _, _ = _client(w)
    assert client.post("/resume/semantic-search", json={"query": SQ}).status_code == 403
    assert client.post("/resume/semantic-search", headers=headers,
                       json={"query": "ab"}).status_code == 422
    assert client.get("/resume/semantic-search", params={"query": "ab"}).status_code == 422
    assert client.get("/resume/semantic-search").status_code == 422
    r = client.post("/resume/semantic-search", headers=headers,
                    json={"query": SQ, "provider_id": 9999})
    assert r.status_code == 404


def test_semantic_search_flow_over_http():
    w = _world()
    client, headers, _, _ = _client(w)
    w.svc.schedule_skills("EMP-1", "u")
    w.run_all()
    r = client.post("/resume/semantic-search", headers=headers,
                    json={"query": SQ, "technology": ["SQL"], "limit": 5})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "running" and body["mode"] == "fallback" and body["notice"]
    assert [i["employee_key"] for i in body["items"]] == ["EMP-1"]
    assert body["items"][0]["percent"] is None and body["items"][0]["source"] == "fallback"
    w.run_all()
    got = client.get("/resume/semantic-search",
                     params=[("query", SQ), ("technology", "sql")]).json()
    assert got["status"] == "done" and got["mode"] == "llm" and got["notice"] is None
    item = got["items"][0]
    assert item["source"] == "llm" and item["percent"] == 90 and item["rationale"]
    assert item["gaps"] == ["нет опыта Kafka (K1)", "нет домена банки"]
    assert item["full_name"] == "Сотрудник-01" and item["cv_file_id"]
    # без фильтра: сотрудник без совпадений по технологиям и тексту в выдачу не попадает
    wide = client.get("/resume/semantic-search", params={"query": SQ}).json()
    assert wide["total"] == 1 and wide["evaluated"] == 1 and wide["candidates_total"] == 2


def test_semantic_search_without_llm_is_a_clear_message_not_an_error():
    w = _world()
    w.env.repo.providers.clear()
    client, headers, _, _ = _client(w)
    r = client.post("/resume/semantic-search", headers=headers, json={"query": SQ})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "unavailable" and "недоступен" in body["notice"]
    assert body["total"] == 1 and w.llm_client.calls == []
    # текстовый поиск и фильтры «Резюме» работают без LLM
    assert client.get("/resume/employees", params={"q": "другое"}).json()["total"] == 1
