"""API шлюза LLM: права (настройки - только админ), CSRF, ключи не возвращаются, поток
«задача -> предпросмотр -> подтверждение -> результат», ошибки. БД не нужна."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import AppUser
from app.auth.sessions import csrf_token, hash_session_id, new_session_id
from app.llm_gateway.router import router

from ._llm_fakes import FAKE_PROVIDER_KEY, NOW, Env

COMPANY = "Тестовая Компания Альфа"
NEW_KEY = "test-new-key-2222222222"


class AuthRepo:
    def __init__(self) -> None:
        self.users = {"boss": "admin", "ivan": "user", "petr": "user"}
        self.sessions: dict[bytes, str] = {}

    def get_user(self, login):
        return AppUser(login, self.users[login], True)

    def get_active_session(self, h):
        return (self.sessions[h], NOW) if h in self.sessions else None

    def touch_session(self, h): ...

    def revoke_session(self, h): ...


@pytest.fixture()
def ctx():
    e = Env()
    e.repo.sources["companies"] = [COMPANY]
    app = FastAPI()
    app.include_router(router)
    app.state.settings = e.settings()
    app.state.repo = AuthRepo()
    app.state.llm = e.gateway
    app.state.llm_repo = e.repo
    app.state.llm_providers = e.providers
    app.state.llm_dictionary = e.dictionary
    return app, e


def client(ctx, login):
    app, _ = ctx
    sid = new_session_id()
    app.state.repo.sessions[hash_session_id(sid)] = login
    c = TestClient(app)
    c.cookies.set("re_session", sid)
    return c, {"X-CSRF-Token": csrf_token(app.state.settings.enc_key_bytes(), sid)}


# --- права ---

def test_settings_admin_only_and_anonymous_401(ctx):
    c, h = client(ctx, "ivan")
    pid = ctx[1].external.id
    assert c.get("/admin/llm/providers").status_code == 403
    assert c.post("/admin/llm/providers", headers=h, json={
        "name": "X", "url": "https://x.example.test/v1", "api_key": NEW_KEY}).status_code == 403
    assert c.patch(f"/admin/llm/providers/{pid}", headers=h, json={"timeout_sec": 5}
                   ).status_code == 403
    assert c.delete(f"/admin/llm/providers/{pid}", headers=h).status_code == 403
    assert c.get("/admin/llm/dictionary").status_code == 403
    assert c.put("/admin/llm/dictionary", headers=h, json={}).status_code == 403
    assert TestClient(ctx[0]).get("/admin/llm/providers").status_code == 401
    assert TestClient(ctx[0]).get("/llm/providers").status_code == 401
    assert len(ctx[1].repo.providers) == 2  # ничего не изменилось


def test_mutations_need_csrf(ctx):
    c, _ = client(ctx, "boss")
    pid = ctx[1].external.id
    assert c.patch(f"/admin/llm/providers/{pid}", json={"timeout_sec": 5}).status_code == 403
    assert c.post("/llm/tasks", json={"purpose": "chat", "prompt": "x"}).status_code == 403
    assert c.get("/admin/llm/providers").status_code == 200


# --- провайдеры ---

def test_provider_list_never_returns_key(ctx):
    c, _ = client(ctx, "boss")
    r = c.get("/admin/llm/providers")
    assert r.status_code == 200
    body = r.text
    assert FAKE_PROVIDER_KEY not in body and "api_key" not in body
    rows = {p["name"]: p for p in r.json()}
    assert rows["Внешняя модель"]["has_key"] is True
    assert rows["Qwen (внутренняя)"]["is_default"] is True
    assert rows["Qwen (внутренняя)"]["is_external"] is False


def test_public_list_has_no_url_or_key_info(ctx):
    c, _ = client(ctx, "ivan")
    rows = c.get("/llm/providers").json()
    assert {set(r) == {"id", "name", "is_external", "is_default"} for r in rows} == {True}


def test_create_update_key_and_delete(ctx):
    c, h = client(ctx, "boss")
    r = c.post("/admin/llm/providers", headers=h, json={
        "name": "Claude", "url": "https://api.example.test/v1", "api_key": NEW_KEY,
        "timeout_sec": 90, "model": "claude-test"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_key"] is True and NEW_KEY not in r.text and body["timeout_sec"] == 90
    pid = body["id"]
    assert ctx[1].store.load_llm_key(pid) == NEW_KEY
    assert NEW_KEY.encode() not in ctx[1].secrets.llm[pid].ciphertext

    r = c.patch(f"/admin/llm/providers/{pid}", headers=h, json={"timeout_sec": 30})
    assert r.status_code == 200 and r.json()["timeout_sec"] == 30
    assert ctx[1].store.load_llm_key(pid) == NEW_KEY  # ключ не затронут

    r = c.patch(f"/admin/llm/providers/{pid}", headers=h, json={"api_key": "test-rotated-key-33"})
    assert r.status_code == 200 and "test-rotated-key-33" not in r.text
    assert ctx[1].store.load_llm_key(pid) == "test-rotated-key-33"

    assert c.delete(f"/admin/llm/providers/{pid}", headers=h).status_code == 204
    assert ctx[1].store.load_llm_key(pid) is None
    assert c.delete(f"/admin/llm/providers/{pid}", headers=h).status_code == 404


@pytest.mark.parametrize("payload, status", [
    ({"name": "A", "url": "http://insecure.example.test/v1", "api_key": NEW_KEY}, 422),  # http
    ({"name": "A", "url": "ftp://x.example.test", "api_key": NEW_KEY}, 422),
    ({"name": "A", "url": "https://x.example.test/v1"}, 422),  # внешний без ключа
    ({"name": "A", "url": "https://x.example.test/v1", "api_key": NEW_KEY,
      "timeout_sec": 0}, 422),
    ({"name": "A", "url": "https://x.example.test/v1", "api_key": NEW_KEY,
      "timeout_sec": 99999}, 422),
    ({"name": "Внешняя модель", "url": "https://x.example.test/v1", "api_key": NEW_KEY}, 409),
])
def test_create_validation(ctx, payload, status):
    c, h = client(ctx, "boss")
    assert c.post("/admin/llm/providers", headers=h, json=payload).status_code == status
    assert len(ctx[1].repo.providers) == 2


def test_internal_http_allowed_without_key(ctx):
    c, h = client(ctx, "boss")
    r = c.post("/admin/llm/providers", headers=h, json={
        "name": "Локальная", "url": "http://llm.internal.test:3000/api/v1",
        "is_external": False})
    assert r.status_code == 201 and r.json()["has_key"] is False


def test_default_switch_and_guards(ctx):
    c, h = client(ctx, "boss")
    e = ctx[1]
    r = c.patch(f"/admin/llm/providers/{e.external.id}", headers=h, json={"is_default": True})
    assert r.status_code == 200 and r.json()["is_default"] is True
    defaults = [p for p in c.get("/admin/llm/providers").json() if p["is_default"]]
    assert [p["id"] for p in defaults] == [e.external.id]
    # снять признак просто так нельзя; удалить провайдера по умолчанию нельзя
    assert c.patch(f"/admin/llm/providers/{e.external.id}", headers=h,
                   json={"is_default": False}).status_code == 409
    assert c.delete(f"/admin/llm/providers/{e.external.id}", headers=h).status_code == 409


def test_cannot_make_external_without_key_or_drop_key(ctx):
    c, h = client(ctx, "boss")
    e = ctx[1]
    assert c.patch(f"/admin/llm/providers/{e.internal.id}", headers=h,
                   json={"is_external": True}).status_code == 422
    assert c.patch(f"/admin/llm/providers/{e.external.id}", headers=h,
                   json={"remove_key": True}).status_code == 422
    assert c.patch(f"/admin/llm/providers/{e.external.id}", headers=h,
                   json={"url": "http://plain.example.test"}).status_code == 422


def test_patch_not_found(ctx):
    c, h = client(ctx, "boss")
    assert c.patch("/admin/llm/providers/999", headers=h, json={"model": "x"}).status_code == 404


# --- словарь ---

def test_dictionary_roundtrip_and_effect(ctx):
    c, h = client(ctx, "boss")
    r = c.put("/admin/llm/dictionary", headers=h, json={
        "companies": ["Дельта-Тест", "  Дельта-Тест "], "projects": ["Эпсилон-Тест"]})
    assert r.status_code == 200
    got = c.get("/admin/llm/dictionary").json()
    assert got["projects"] == ["Эпсилон-Тест"] and got["persons"] == []
    u, uh = client(ctx, "ivan")
    r = u.post("/llm/tasks", headers=uh, json={
        "purpose": "chat", "prompt": "Эпсилон-Тест для Дельта-Тест",
        "provider_id": ctx[1].external.id})
    assert r.status_code == 201
    assert r.json()["preview"]["prompt"] == "[ПРОЕКТ_1] для [КОМПАНИЯ_1]"


# --- задачи ---

def test_internal_task_flow(ctx):
    c, h = client(ctx, "ivan")
    r = c.post("/llm/tasks", headers=h, json={
        "purpose": "match", "prompt": f"Запрос про {COMPANY}", "ref": "ANALYTICS-1"})
    assert r.status_code == 201
    t = r.json()
    assert t["status"] == "queued" and t["requires_confirmation"] is False
    assert t["preview"] is None and t["is_external"] is False and t["timeout_sec"] == 120
    ctx[1].gateway.run_next()
    t = c.get(f"/llm/tasks/{t['id']}").json()
    assert t["status"] == "done" and COMPANY in t["result"]
    assert ctx[1].client.calls[0]["prompt"] == f"Запрос про {COMPANY}"  # без обезличивания


def test_external_task_flow_with_preview_and_confirm(ctx):
    c, h = client(ctx, "ivan")
    r = c.post("/llm/tasks", headers=h, json={
        "purpose": "match", "prompt": f"Кандидат для {COMPANY}, tester@example.test",
        "provider_id": ctx[1].external.id})
    t = r.json()
    assert t["status"] == "awaiting_confirmation" and t["requires_confirmation"] is True
    assert COMPANY not in r.text and "tester@example.test" not in r.text
    assert t["preview"]["prompt"] == "Кандидат для [КОМПАНИЯ_1], [EMAIL_1]"
    assert ctx[1].gateway.run_next() is None and ctx[1].client.calls == []

    r = c.post(f"/llm/tasks/{t['id']}/confirm", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert c.post(f"/llm/tasks/{t['id']}/confirm", headers=h).status_code == 409
    ctx[1].gateway.run_next()
    done = c.get(f"/llm/tasks/{t['id']}").json()
    assert done["status"] == "done" and COMPANY in done["result"]


def test_tasks_of_other_user_hidden(ctx):
    c, h = client(ctx, "ivan")
    tid = c.post("/llm/tasks", headers=h, json={"purpose": "chat", "prompt": "x"}).json()["id"]
    p, ph = client(ctx, "petr")
    assert p.get(f"/llm/tasks/{tid}").status_code == 404
    assert p.post(f"/llm/tasks/{tid}/cancel", headers=ph).status_code == 404
    assert p.get("/llm/tasks").json() == []
    assert [t["id"] for t in c.get("/llm/tasks").json()] == [tid]


def test_timeout_then_retry_over_api(ctx):
    from app.llm_gateway.client import LlmTimeoutError

    from ._llm_fakes import FakeClient

    c, h = client(ctx, "ivan")
    e = ctx[1]
    e.gateway._client = FakeClient(error=LlmTimeoutError(120))
    tid = c.post("/llm/tasks", headers=h, json={"purpose": "chat", "prompt": "x"}).json()["id"]
    e.gateway.run_next()
    t = c.get(f"/llm/tasks/{tid}").json()
    assert t["status"] == "timeout" and "Повторите" in t["error_message"]
    e.gateway._client = FakeClient()
    assert c.post(f"/llm/tasks/{tid}/retry", headers=h).json()["status"] == "queued"
    e.gateway.run_next()
    assert c.get(f"/llm/tasks/{tid}").json()["status"] == "done"


def test_validation_and_errors(ctx):
    c, h = client(ctx, "ivan")
    bad = [{"purpose": "Bad Purpose", "prompt": "x"}, {"purpose": "chat", "prompt": ""},
           {"purpose": "chat", "prompt": "x" * 200_001}]
    for body in bad:
        assert c.post("/llm/tasks", headers=h, json=body).status_code == 422
    r = c.post("/llm/tasks", headers=h, json={"purpose": "chat", "prompt": "x",
                                              "provider_id": 999})
    assert r.status_code == 404
    assert c.get("/llm/tasks/not-a-uuid").status_code == 404
    assert c.post("/llm/tasks/not-a-uuid/retry", headers=h).status_code == 404


def test_503_when_gateway_not_configured():
    app = FastAPI()
    app.include_router(router)
    app.state.repo = AuthRepo()
    from app.config import Secret, Settings

    from ._llm_fakes import ENC_KEY_B64

    app.state.settings = Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(ENC_KEY_B64))
    sid = new_session_id()
    app.state.repo.sessions[hash_session_id(sid)] = "ivan"
    c = TestClient(app)
    c.cookies.set("re_session", sid)
    h = {"X-CSRF-Token": csrf_token(app.state.settings.enc_key_bytes(), sid)}
    assert c.post("/llm/tasks", headers=h, json={"purpose": "chat", "prompt": "x"}
                  ).status_code == 503
