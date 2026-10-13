"""KON-47 (Pre.QA-2), Q47-10 и часть Q47-09: сопоставление кандидатов и поиск по смыслу на стенде.

Требования: FR-005, FR-036, FR-032, FR-023 (внешний провайдер для сопоставления), NFR-010 (деградация),
FR-033 (заметка: в API нет записи, см. отчёт). LLM - фейк стенда (оценка детерминирована по пересечению
технологий), поэтому проверяются порядок, отбор, кэш, подтверждение и деградация, а не качество оценки.
"""
from __future__ import annotations

import time

import pytest

from ._fakes import EXT_KEY, JiraCtl, LlmCtl, make_docx, requires_fakes, wait_until
from ._stage1 import (cleanup_files, cleanup_llm, cleanup_matching, ensure_allow_list,
                      make_external_provider, prepare_cache)
from ._stand import Session, logged_in, requires_stand
from .test_stage1_cv_qa import upload

pytestmark = [requires_stand, requires_fakes]

NEED = "ANALYTICS-1001"          # требования: «Системный анализ, REST, SQL, Kafka»
CVS = {                           # сотрудник -> стек в основном CV
    "ANALYTICS-5001": ("SQL, BPMN, UML, REST, Kafka",),
    "ANALYTICS-5002": ("Python, Java",),
    "ANALYTICS-5003": ("SQL, Kafka, REST, интеграции",),
    "ANALYTICS-5004": ("BPMN, UML",),
    "ANALYTICS-5005": ("SQL, REST, Kafka",),          # статус «Коммерческий проект»: не назначается
}
ASSIGNABLE = {"ANALYTICS-5001", "ANALYTICS-5002", "ANALYTICS-5003", "ANALYTICS-5004"}
EXT_NAME = "QA-ext-match"
DONE = ("done", "partial", "failed")


@pytest.fixture(scope="module")
def admin():
    a = ensure_allow_list()
    yield a
    a.close()


@pytest.fixture(scope="module")
def user(admin):
    JiraCtl().reset()
    s = logged_in("qa-user")
    prepare_cache(s)
    cleanup_files(list(CVS))
    cleanup_matching()
    cleanup_llm((EXT_NAME,))
    LlmCtl().reset()
    yield s
    cleanup_files(list(CVS))
    cleanup_matching()
    cleanup_llm((EXT_NAME,))
    LlmCtl().reset()
    s.close()


@pytest.fixture(scope="module")
def with_cvs(user):
    for key, stack in CVS.items():
        r = upload(user, key, "main_cv", f"cv_{key}.docx", make_docx("Профиль", "Основной стек", *stack))
        assert r.status_code == 201, r.text

    def _ready():
        rows = user.http.get("/resume/employees", params={"limit": 200}).json()["items"]
        got = {i["employee_key"]: i for i in rows if i["employee_key"] in CVS}
        return len(got) == len(CVS) and all(i["skills_status"] == "ok" for i in got.values())
    wait_until(_ready, timeout=90, interval=2, what="навыки всех CV")
    yield


@pytest.fixture()
def llm():
    c = LlmCtl()
    c.reset()
    yield c
    c.reset()


def state(s: Session, key: str = NEED, **params) -> dict:
    return s.http.get(f"/needs/{key}/match", params=params).json()


def wait_state(s: Session, key: str = NEED, timeout: float = 60) -> dict:
    def _d():
        st = state(s, key, limit=100)
        return st if st["status"] in DONE else None
    return wait_until(_d, timeout=timeout, interval=1, what=f"сопоставление {key}")


def match_requests(llm: LlmCtl) -> list[dict]:
    return [c for c in llm.captured() if c["kind"] == "match"]


def test_fr005_need_without_candidates_with_cv(user, llm):
    """Нет сотрудников с основным CV: понятный результат без вызова LLM (FR-005, «нет кандидатов»)."""
    r = user.post(f"/needs/{NEED}/match", json={})
    assert r.status_code == 202, r.text
    st = r.json()
    assert st["candidates_total"] == 0 and st["items"] == [] and st["tasks"] == []
    assert st["status"] == "none" and st["run_id"] is None                 # прогона нет, UI показывает «нет кандидатов»
    time.sleep(3)
    assert match_requests(llm) == []


def test_fr005_never_matched_need_has_status_none(user):
    st = state(user, "ANALYTICS-1013")
    assert st["status"] == "none" and st["items"] == [] and st["calculated_at"] is None
    assert user.http.get("/needs/ANALYTICS-99999/match").status_code == 404
    assert user.http.get("/needs/bad_key/match").status_code == 422
    assert Session().http.get(f"/needs/{NEED}/match").status_code == 401


def test_fr005_match_sorted_desc_only_assignable_with_cv(user, with_cvs, llm):
    r = user.post(f"/needs/{NEED}/match", json={"force": True})
    assert r.status_code == 202, r.text
    st = wait_state(user)
    assert st["status"] == "done" and st["evaluated"] == st["candidates_total"] == 4
    keys = [i["employee_key"] for i in st["items"]]
    assert set(keys) == ASSIGNABLE and "ANALYTICS-5005" not in keys       # FR-002: не назначаемые не попадают
    pct = [i["percent"] for i in st["items"]]
    assert pct == sorted(pct, reverse=True)
    assert all(0 <= p <= 100 for p in pct)
    for i in st["items"]:
        assert i["rationale"] and i["cv_file_id"] and i["stale"] is False
        assert isinstance(i["gaps"], list) and len(i["gaps"]) <= 5
    assert st["model"] == "fake-qwen" and st["calculated_at"]
    # выше всех - у кого пересекаются технологии потребности (SQL, REST, Kafka)
    assert st["items"][0]["employee_key"] in {"ANALYTICS-5001", "ANALYTICS-5003"}
    assert st["items"][-1]["employee_key"] in {"ANALYTICS-5002", "ANALYTICS-5004"}
    mc = user.http.get("/needs/match-counts", params={"keys": [NEED, "ANALYTICS-1013"]}).json()["counts"]
    assert mc == {NEED: 4}                                                 # у не сопоставленной потребности записи нет


def test_fr036_repeat_uses_cache_force_recalculates(user, with_cvs, llm):
    r = user.post(f"/needs/{NEED}/match", json={})
    assert r.status_code == 202
    st = wait_state(user)
    assert st["status"] == "done" and st["evaluated"] == 4
    time.sleep(3)
    assert match_requests(llm) == []                                       # из кэша: LLM не вызывалась
    r = user.post(f"/needs/{NEED}/match", json={"force": True})
    assert r.status_code == 202
    wait_until(lambda: match_requests(llm), timeout=30, what="вызов LLM после «Пересчитать»")


def test_fr036_show_more_pagination(user, with_cvs):
    first = state(user, limit=2, offset=0)
    second = state(user, limit=2, offset=2)
    assert first["total"] == second["total"] == 4
    a = [i["employee_key"] for i in first["items"]]
    b = [i["employee_key"] for i in second["items"]]
    assert len(a) == len(b) == 2 and not set(a) & set(b)
    full = [i["employee_key"] for i in state(user, limit=100)["items"]]
    assert a + b == full
    assert user.http.get(f"/needs/{NEED}/match", params={"limit": 101}).status_code == 422
    assert user.http.get(f"/needs/{NEED}/match", params={"limit": 0}).status_code == 422


def test_fr005_internal_prompt_has_labels_without_names_or_keys(user, with_cvs, llm):
    names = [e["full_name"] for e in user.http.get("/sync/employees", params={"limit": 200}).json()["items"]
             if e["jira_key"] in CVS]
    user.post(f"/needs/{NEED}/match", json={"force": True})
    wait_until(lambda: match_requests(llm), timeout=30, what="запрос оценки")
    wait_state(user)
    sent = match_requests(llm)[-1]
    assert "### K1" in sent["prompt"] and "Системный аналитик в проект" in sent["prompt"]
    for n in names:
        assert n not in sent["prompt"], n
    for k in CVS:
        assert k not in sent["prompt"]
    assert sent["auth"].startswith("Bearer FAKE_internal")


def test_fr005_fr023_external_provider_match_needs_run_confirmation(user, admin, with_cvs, llm):
    """Внешний провайдер: прогон ждёт одного подтверждения; до него на провод ничего не уходит; в
    запросе обезличенный текст потребности, ФИО сотрудников отсутствуют."""
    p = make_external_provider(admin, EXT_NAME, key=EXT_KEY)
    r = user.post(f"/needs/{NEED}/match", json={"provider_id": p["id"], "force": True})
    assert r.status_code == 202, r.text
    st = r.json()
    assert st["status"] == "awaiting_confirmation" and st["run_id"]
    assert all(t["requires_confirmation"] for t in st["tasks"])
    time.sleep(5)
    assert match_requests(llm) == []
    run = user.http.get(f"/matching/runs/{st['run_id']}").json()
    assert run["is_external"] is True and run["requires_confirmation"] is True and run["tasks"]
    preview = [t["prompt"] for t in run["tasks"]]
    names = [e["full_name"] for e in user.http.get("/sync/employees", params={"limit": 200}).json()["items"]]
    assert all(n not in pr for pr in preview for n in names)
    c = user.post(f"/matching/runs/{st['run_id']}/confirm")
    assert c.status_code == 200, c.text
    done = wait_state(user)
    assert done["status"] == "done" and done["evaluated"] == 4
    sent = match_requests(llm)
    assert [x["prompt"] for x in sent] == preview                          # на проводе ровно показанное
    assert sent[0]["auth"] == f"Bearer {EXT_KEY}"
    assert user.post(f"/matching/runs/{st['run_id']}/confirm").status_code == 409
    assert Session().http.get(f"/matching/runs/{st['run_id']}").status_code == 401
    other = logged_in("qa-revoked")
    assert other.http.get(f"/matching/runs/{st['run_id']}").status_code == 404   # чужой прогон не виден


def test_fr005_cancel_run_before_confirm(user, admin, with_cvs, llm):
    pr = next(p for p in admin.http.get("/admin/llm/providers").json() if p["name"] == EXT_NAME)
    st = user.post(f"/needs/{NEED}/match", json={"provider_id": pr["id"], "force": True}).json()
    assert st["status"] == "awaiting_confirmation"
    assert user.post(f"/matching/runs/{st['run_id']}/cancel").status_code == 200
    assert user.post(f"/matching/runs/{st['run_id']}/confirm").status_code == 409
    time.sleep(4)
    assert match_requests(llm) == []


def test_fr005_llm_failure_is_reported_and_recoverable(user, with_cvs, llm):
    llm.mode("error500")
    r = user.post(f"/needs/{NEED}/match", json={"force": True})
    assert r.status_code == 202, r.text
    st = wait_state(user)
    assert st["status"] in ("failed", "partial") and st["errors"] and st["batches_failed"] >= 1
    assert not any(n in e for e in st["errors"] for n in ("Иванов", "Сидоров", "Основной стек"))
    llm.mode("ok")
    assert user.post(f"/needs/{NEED}/match", json={"force": True}).status_code == 202
    st = wait_state(user)
    assert st["status"] == "done" and st["evaluated"] == 4


def test_fr005_garbage_answer_does_not_break_state(user, with_cvs, llm):
    llm.mode("garbage")
    assert user.post(f"/needs/{NEED}/match", json={"force": True}).status_code == 202
    st = wait_state(user)
    assert st["status"] in ("failed", "partial")
    llm.mode("ok")
    assert user.post(f"/needs/{NEED}/match", json={"force": True}).status_code == 202
    assert wait_state(user)["status"] == "done"


def test_fr032_semantic_search_llm_then_fallback_without_llm(user, with_cvs, llm):
    q = "нужен аналитик SQL и Kafka"
    r = user.post("/resume/semantic-search", json={"query": q, "limit": 10, "force": True})
    assert r.status_code == 202, r.text

    def _done():
        s = user.http.get("/resume/semantic-search", params={"query": q}).json()
        return s if s["status"] in DONE else None
    st = wait_until(_done, timeout=60, interval=1, what="поиск по смыслу")
    assert st["status"] == "done" and st["mode"] == "llm"
    assert st["items"] and all(i["source"] == "llm" for i in st["items"][:2])
    pcts = [i["percent"] for i in st["items"] if i["percent"] is not None]
    assert pcts == sorted(pcts, reverse=True)
    # LLM недоступна: список по технологиям и тексту, причина понятна пользователю
    llm.mode("error500")
    q2 = "нужен аналитик Python Java"
    assert user.post("/resume/semantic-search", json={"query": q2, "limit": 10, "force": True}).status_code == 202

    def _fb():
        s = user.http.get("/resume/semantic-search", params={"query": q2}).json()
        return s if s["status"] in DONE + ("unavailable",) else None
    fb = wait_until(_fb, timeout=60, interval=1, what="деградация поиска")
    assert fb["mode"] == "fallback" and fb["notice"] and fb["items"]
    assert "ANALYTICS-5002" in {i["employee_key"] for i in fb["items"]}
    assert all(i["source"] != "llm" for i in fb["items"])


def test_fr032_query_validation(user):
    assert user.post("/resume/semantic-search", json={"query": "ab"}).status_code == 422
    assert user.post("/resume/semantic-search", json={"query": "x" * 501}).status_code == 422


def test_fr005_matching_settings_admin_only_and_bounds(user, admin):
    assert user.http.get("/admin/matching/settings").status_code == 403
    cur = admin.http.get("/admin/matching/settings").json()
    assert cur["deep_size"] == 30 and cur["screen_batch"] == 50
    for body in ({"deep_size": 4}, {"deep_size": 101}, {"screen_batch": 9}, {"screen_batch": 101}):
        assert admin.http.put("/admin/matching/settings", json=body, headers=admin.csrf()).status_code == 422, body
    ok = admin.http.put("/admin/matching/settings", json={"deep_size": 40}, headers=admin.csrf())
    assert ok.status_code == 200 and ok.json()["deep_size"] == 40
    assert admin.http.put("/admin/matching/settings", json={"deep_size": 30}, headers=admin.csrf()).status_code == 200


def test_fr033_note_has_no_write_api_documented_limitation(user):
    """FR-033 (Could): записи заметки в API нет - есть только чтение полей note*. Фиксируем факт,
    чтобы появление записи потребовало обновить проверки; решение - за аналитиком (отчёт, п. 8.2)."""
    for method, url in (("put", "/resume/employees/ANALYTICS-5001/note"),
                        ("post", "/resume/employees/ANALYTICS-5001/note"),
                        ("patch", "/sync/employees/ANALYTICS-5001")):
        r = getattr(user.http, method)(url, json={"note": "x"}, headers=user.csrf())
        assert r.status_code in (404, 405), (method, url, r.status_code)
