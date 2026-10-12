"""API двухступенчатой оценки (KON-38): «Оценить следующих», прогон целиком (предпросмотр и одно
подтверждение), настройки администратора. БД не нужна: репозитории в памяти."""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.matching.router import router as matching_router

from ._cv_fakes import make_client
from ._matching_fakes import World
from .test_cv_api import FakeCvRepo, FakeLimits

NEED = "ANALYTICS-1"
USER = "ivan"  # владелец сессии тестового клиента


def _by_skills(prompt: str) -> str:
    out, label = [], None
    for ln in prompt.splitlines():
        if ln.startswith("### "):
            label = ln[4:].strip()
        elif ln.startswith("Навыки из CV:") and label:
            out.append({"id": label, "percent": 90 if "sql" in ln else 10, "rationale": "ok"})
    return json.dumps(out)


def _world(n=8, **kw) -> World:
    w = World(max_evaluated=3, **kw)
    w.needs.add(NEED, title="Нужен аналитик SQL", description="BPMN", requirements=None)
    for i in range(1, n + 1):
        fid = w.repo.add_employee(f"EMP-{i}", f"Сотрудник-{i:02d}", text=f"Резюме номер {i}")
        w.repo.skills[fid] = ["sql"] if i > n - 3 else ["java"]
    w.match_reply = _by_skills
    return w


def _client(world: World, role: str = "user"):
    client, headers, app = make_client(FakeCvRepo(), FakeLimits(), role=role)
    app.include_router(matching_router)
    app.state.matching = world.svc
    return client, headers


def test_match_next_flow_and_fields_over_http():
    w = _world()
    client, h = _client(w)
    r = client.post(f"/needs/{NEED}/match", headers=h, json={})
    assert r.status_code == 202
    body = r.json()
    assert body["stage"] == "screening" and body["run_id"] and body["deep_size"] == 3
    assert body["tasks"][0]["stage"] == "screening" and body["tasks"][0]["run_id"] == body["run_id"]
    w.run_all()
    got = client.get(f"/needs/{NEED}/match").json()
    assert got["status"] == "done" and got["stage"] == "deep" and got["evaluated"] == 3
    assert got["remaining"] == 5 and got["screened"] == 8 and got["screen_total"] == 8
    assert got["can_evaluate_next"] is True

    assert client.post(f"/needs/{NEED}/match/next").status_code == 403       # без CSRF
    r = client.post(f"/needs/{NEED}/match/next", headers=h)
    assert r.status_code == 202 and r.json()["stage"] == "deep"
    assert r.json()["can_evaluate_next"] is False                            # прогон идёт
    w.run_all()
    got = client.get(f"/needs/{NEED}/match").json()
    assert got["evaluated"] == 6 and got["remaining"] == 2
    assert client.post("/needs/ANALYTICS-404/match/next", headers=h).status_code == 404


def test_next_requires_login():
    w = _world()
    app = FastAPI()
    app.include_router(matching_router)
    app.state.matching = w.svc
    c = TestClient(app)
    assert c.post(f"/needs/{NEED}/match/next").status_code == 401
    assert c.get("/matching/runs/00000000-0000-4000-8000-000000000000").status_code == 401
    assert c.post("/matching/runs/00000000-0000-4000-8000-000000000000/confirm").status_code == 401


def test_run_preview_and_single_confirm_over_http_for_external_provider():
    w = _world(external_default=True)
    client, h = _client(w)
    st = client.post(f"/needs/{NEED}/match", headers=h, json={}).json()
    assert st["status"] == "awaiting_confirmation" and st["stage"] == "screening"
    run_id = st["run_id"]

    run = client.get(f"/matching/runs/{run_id}").json()
    assert run["kind"] == "match" and run["stage"] == "screening" and run["is_external"]
    assert run["requires_confirmation"] is True and run["candidates"] == 8
    assert run["tasks"] and run["tasks"][0]["prompt"] and run["need_key"] == NEED
    assert "Сотрудник-" not in json.dumps(run, ensure_ascii=False)
    assert w.llm_client.calls == []

    assert client.post(f"/matching/runs/{run_id}/confirm").status_code == 403    # без CSRF
    ok = client.post(f"/matching/runs/{run_id}/confirm", headers=h)
    assert ok.status_code == 200 and ok.json()["requires_confirmation"] is False
    assert ok.json()["status"] == "running"
    again = client.post(f"/matching/runs/{run_id}/confirm", headers=h)
    assert again.status_code == 409                                              # уже подтверждён

    w.run_all()                                                                  # ступень 2 ждёт
    nxt = client.get(f"/needs/{NEED}/match").json()
    assert nxt["status"] == "awaiting_confirmation" and nxt["stage"] == "deep"
    assert nxt["run_id"] != run_id
    assert client.post(f"/matching/runs/{nxt['run_id']}/confirm", headers=h).status_code == 200


def test_run_cancel_and_unknown_or_malformed_run():
    w = _world(external_default=True)
    client, h = _client(w)
    st = client.post(f"/needs/{NEED}/match", headers=h, json={}).json()
    r = client.post(f"/matching/runs/{st['run_id']}/cancel", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert client.get("/matching/runs/00000000-0000-4000-8000-000000000000").status_code == 404
    assert client.get("/matching/runs/abc").status_code == 422
    assert client.post("/matching/runs/00000000-0000-4000-8000-000000000000/confirm",
                       headers=h).status_code == 404


def test_semantic_search_next_and_run_fields():
    w = _world()
    client, h = _client(w)
    body = {"query": "аналитик sql", "limit": 50}
    params = body
    r = client.post("/resume/semantic-search", headers=h, json=body)
    assert r.status_code == 202 and r.json()["stage"] == "screening" and r.json()["run_id"]
    w.run_all()
    got = client.get("/resume/semantic-search", params=params).json()
    assert got["stage"] == "deep" and got["evaluated"] == 3 and got["deep_size"] == 3
    assert got["remaining"] == 5 and got["can_evaluate_next"] is True
    assert {i["source"] for i in got["items"]} == {"llm", "screening"}
    assert all(i["screen_percent"] is not None for i in got["items"] if i["source"] == "screening")
    assert client.post("/resume/semantic-search/next", json=body).status_code == 403
    r = client.post("/resume/semantic-search/next", headers=h, json=body)
    assert r.status_code == 202 and r.json()["stage"] == "deep"
    w.run_all()
    got = client.get("/resume/semantic-search", params=params).json()
    assert got["evaluated"] == 6
    assert client.post("/resume/semantic-search/next", headers=h,
                       json={"query": "ab"}).status_code == 422


def test_admin_settings_only_for_admin_with_csrf_and_bounds():
    w = _world()
    user, uh = _client(w)
    assert user.get("/admin/matching/settings").status_code == 403
    denied = user.put("/admin/matching/settings", headers=uh, json={"deep_size": 10})
    assert denied.status_code == 403

    admin, ah = _client(w, role="admin")
    got = admin.get("/admin/matching/settings").json()
    assert got["deep_size"] == 3 and got["screen_batch"] == 50                  # из World
    assert (got["deep_size_min"], got["deep_size_max"]) == (5, 100)
    assert (got["screen_batch_min"], got["screen_batch_max"]) == (10, 100)
    assert admin.put("/admin/matching/settings", json={"deep_size": 10}).status_code == 403  # CSRF
    ok = admin.put("/admin/matching/settings", headers=ah, json={"deep_size": 40})
    assert ok.status_code == 200 and ok.json()["deep_size"] == 40
    assert ok.json()["screen_batch"] == 50
    ok = admin.put("/admin/matching/settings", headers=ah, json={"screen_batch": 25})
    assert ok.json() == {**ok.json(), "deep_size": 40, "screen_batch": 25}
    for bad in ({"deep_size": 4}, {"deep_size": 101}, {"screen_batch": 9},
                {"screen_batch": 101}, {"deep_size": "много"}):
        assert admin.put("/admin/matching/settings", headers=ah, json=bad).status_code == 422
    assert admin.get("/admin/matching/settings").json()["deep_size"] == 40
    # пользователям значение N видно в состоянии потребности
    assert user.get(f"/needs/{NEED}/match").json()["deep_size"] == 40


# --- правка обезличенного текста прогона до подтверждения (CR владельца, FR-023) ---

OMEGA = "Фирма Омега"


def _edit_world():
    w = _world(2, external_default=True)
    w.env.repo.sources["companies"] = [OMEGA]
    w.repo.add_employee("EMP-20", "Сотрудник-20", text=f"Работал в {OMEGA} аналитиком")
    return w


def _put(client, h, run_id, task_id, text):
    return client.put(f"/matching/runs/{run_id}/tasks/{task_id}/prompt", headers=h,
                      json={"prompt": text})


def test_edit_prompt_over_http_flow():
    w = _edit_world()
    client, h = _client(w)
    run_id = client.post(f"/needs/{NEED}/match", headers=h, json={}).json()["run_id"]
    t = client.get(f"/matching/runs/{run_id}").json()["tasks"][0]
    assert t["editable"] is True and t["edited"] is False
    assert "[КОМПАНИЯ_1]" in t["prompt"] and OMEGA not in t["prompt"]

    new = t["prompt"] + "\nДоп. пожелание: опыт в [КОМПАНИЯ_1] и [ORG_7]."
    url = f"/matching/runs/{run_id}/tasks/{t['task_id']}/prompt"
    assert client.put(url, json={"prompt": new}).status_code == 403            # без CSRF
    ok = _put(client, h, run_id, t["task_id"], new)
    assert ok.status_code == 200
    t2 = ok.json()["tasks"][0]
    assert t2["prompt"] == new and t2["edited"] is True and t2["editable"] is True
    assert client.get(f"/matching/runs/{run_id}").json()["tasks"][0]["prompt"] == new

    # утечка: оригинал подстановки этой задачи; текст не сохраняется
    w.env.repo.sources["companies"] = []
    bad = _put(client, h, run_id, t["task_id"], new + f" {OMEGA}")
    assert bad.status_code == 422
    d = bad.json()["detail"]
    assert d["code"] == "leak_detected" and d["fragments"] and OMEGA in d["fragments"][0]
    assert client.get(f"/matching/runs/{run_id}").json()["tasks"][0]["prompt"] == new
    # длина
    assert _put(client, h, run_id, t["task_id"], "").status_code == 422
    too_long = "я" * (len(t["prompt"]) * 2 + 1)
    assert _put(client, h, run_id, t["task_id"], too_long).status_code == 422

    # confirm и выполнение используют отредактированный текст
    assert client.post(f"/matching/runs/{run_id}/confirm", headers=h).status_code == 200
    w.run_all()
    assert w.llm_client.calls[0]["prompt"] == new
    after = _put(client, h, run_id, t["task_id"], "поздно")
    assert after.status_code == 409


def test_edit_prompt_foreign_and_unknown_task_are_404():
    w = _edit_world()
    client, h = _client(w)                       # пользователь сессии - ivan
    run_id = w.svc.start_match(NEED, "petr").run_id
    task_id = w.svc.run_info("petr", run_id).tasks[0].task_id
    assert _put(client, h, run_id, task_id, "чужой прогон").status_code == 404
    own = client.post(f"/needs/{NEED}/match", headers=h, json={}).json()["run_id"]
    other = "00000000-0000-4000-8000-000000000000"
    assert _put(client, h, own, other, "нет такой задачи").status_code == 404
    assert _put(client, h, own, task_id, "задача чужого прогона").status_code == 404


def test_edit_prompt_internal_provider_is_not_editable():
    w = _world(2)
    client, h = _client(w)
    run_id = client.post(f"/needs/{NEED}/match", headers=h, json={}).json()["run_id"]
    t = client.get(f"/matching/runs/{run_id}").json()["tasks"][0]
    assert t["editable"] is False and t["edited"] is False
    assert _put(client, h, run_id, t["task_id"], "правка").status_code == 409
