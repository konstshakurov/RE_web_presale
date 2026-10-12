"""Шлюз LLM на настоящей БД (KON-37): провайдеры, ключ шифртекстом в secret_store, очередь
задач, словарь, сквозной поток. Нужна TEST_APP_DB_URL (роль app_rw, миграции app_db V001-V008),
иначе пропуск. Данные тестов: провайдеры «kont37-*», пользователь kont37-user; после теста
удаляются."""
from __future__ import annotations

import os

import pytest

from app.app_repo import AppRepo, create_pool
from app.config import Secret
from app.llm_gateway import (
    DictionarySource,
    GatewayError,
    LlmGateway,
    LlmRepo,
    ProviderError,
    ProviderService,
)
from app.llm_gateway.repo import AWAITING, DONE, QUEUED
from app.secrets import SecretStore

from ._llm_fakes import KEY32, FakeClient

DSN = os.environ.get("TEST_APP_DB_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_APP_DB_URL не задан (стенд БД недоступен)")

USER = "kont37-user"
OTHER = "kont37-other"
KEY = "test-db-key-0123456789"
COMPANY = "Контрагент-Тест-37"


def _clean(pool):
    with pool.connection() as c:
        c.execute("DELETE FROM app.llm_task WHERE owner_login IN (%s, %s)", (USER, OTHER))
        c.execute("DELETE FROM app.secret_store WHERE kind = 'llm_key' AND provider_id IN "
                  "(SELECT id FROM app.llm_provider WHERE name LIKE 'kont37-%')")
        c.execute("DELETE FROM app.llm_provider WHERE name LIKE 'kont37-%'")
        c.execute("DELETE FROM app.jira_epic WHERE epic_key = 'KONT37-1'")
        c.execute("DELETE FROM app.app_setting WHERE key = 'llm_dictionary'")
        c.execute("DELETE FROM app.app_user WHERE ldap_login IN (%s, %s)", (USER, OTHER))


@pytest.fixture()
def env():
    pool = create_pool(DSN)
    pool.open(wait=True)
    _clean(pool)
    with pool.connection() as c:
        for u in (USER, OTHER):
            c.execute("INSERT INTO app.app_user (ldap_login, role, active) "
                      "VALUES (%s, 'user', true)", (u,))
        c.execute("INSERT INTO app.jira_epic (epic_key, name) VALUES ('KONT37-1', %s)",
                  (COMPANY,))
    repo = LlmRepo(pool)
    store = SecretStore(AppRepo(pool), KEY32, "k1")
    client = FakeClient()
    gw = LlmGateway(repo, store, DictionarySource(repo), client, env_key=Secret(""))
    # провайдер по умолчанию уже может быть (стенд общий): работаем с явными provider_id
    svc = ProviderService(repo, store)
    yield pool, repo, store, gw, svc, client
    _clean(pool)
    pool.close()


def _ext(svc, name="kont37-ext", **kw):
    return svc.create(name, "https://llm.example.test/v1", is_external=True, api_key=KEY,
                      model="ext-model", **kw)


def test_provider_crud_and_key_encrypted_in_db(env):
    pool, repo, store, _, svc, _ = env
    p = _ext(svc)
    assert p.has_key and p.timeout_sec == 120 and p.model == "ext-model"
    with pool.connection() as c:
        row = c.execute("SELECT ciphertext, nonce, key_id FROM app.secret_store "
                        "WHERE kind = 'llm_key' AND provider_id = %s", (p.id,)).fetchone()
    assert KEY.encode() not in bytes(row[0]) and len(bytes(row[1])) == 12 and row[2] == "k1"
    assert store.load_llm_key(p.id) == KEY

    p2 = svc.update(p.id, timeout_sec=45, model="m2")
    assert p2.timeout_sec == 45 and p2.model == "m2" and p2.has_key
    svc.update(p.id, remove_key=False, api_key="test-db-rotated-key-9")
    assert store.load_llm_key(p.id) == "test-db-rotated-key-9"

    with pytest.raises(ProviderError) as ei:
        _ext(svc)  # то же имя
    assert ei.value.code == "name_taken"
    with pytest.raises(ProviderError) as ei:
        svc.update(p.id, timeout_sec=0)
    assert ei.value.code == "invalid_timeout"

    svc.delete(p.id)
    assert repo.get_provider(p.id) is None
    assert store.load_llm_key(p.id) is None


def test_default_is_single(env):
    _, repo, _, _, svc, _ = env
    before = repo.get_default_provider()
    a = _ext(svc, "kont37-a", is_default=True)
    b = _ext(svc, "kont37-b")
    assert repo.get_default_provider().id == a.id
    svc.update(b.id, is_default=True)
    assert [p.id for p in repo.list_providers() if p.is_default] == [b.id]
    with pytest.raises(ProviderError):
        svc.delete(b.id)  # по умолчанию
    # вернуть прежний default, чтобы не ломать общий стенд
    if before is not None:
        svc.update(before.id, is_default=True)


def test_task_flow_external_with_confirmation_and_restore(env):
    pool, repo, store, gw, svc, client = env
    p = _ext(svc)
    gw._client = client = FakeClient(lambda prompt: "Ответ про [КОМПАНИЯ_1]")
    v = gw.submit(USER, "match", f"Нужен аналитик для {COMPANY}, tester@example.test",
                  provider_id=p.id, ref="ANALYTICS-1")
    assert v.status == AWAITING and COMPANY not in v.preview.prompt
    assert v.preview.prompt == "Нужен аналитик для [КОМПАНИЯ_1], [EMAIL_1]"
    with pool.connection() as c:
        ct = bytes(c.execute("SELECT payload_ct FROM app.llm_task WHERE id = %s::uuid",
                             (v.id,)).fetchone()[0])
    assert COMPANY.encode() not in ct and b"tester" not in ct

    assert client.calls == []
    # чужой пользователь не видит и не подтверждает
    with pytest.raises(GatewayError):
        gw.get(OTHER, v.id)
    gw.confirm(USER, v.id)
    # изолированно выполняем именно нашу задачу
    task = repo.claim_next()
    while task is not None and task.id != v.id:
        repo.finish_error(task.id, "failed", "test", "x")
        task = repo.claim_next()
    assert task is not None
    gw._execute(task)
    done = gw.get(USER, v.id)
    assert done.status == DONE and done.result == f"Ответ про {COMPANY}"
    assert client.calls[0]["prompt"] == "Нужен аналитик для [КОМПАНИЯ_1], [EMAIL_1]"
    assert client.calls[0]["api_key"] == KEY
    with pool.connection() as c:
        row = c.execute("SELECT payload_ct, result_ct FROM app.llm_task WHERE id = %s::uuid",
                        (v.id,)).fetchone()
    assert row[0] is None and COMPANY.encode() not in bytes(row[1])


def test_queue_confirm_cancel_requeue_semantics(env):
    pool, repo, _, gw, svc, _ = env
    p = _ext(svc)
    v = gw.submit(USER, "chat", "запрос", provider_id=p.id)
    assert not repo.requeue_task(v.id)  # не в failed/timeout
    assert repo.confirm_task(v.id) and not repo.confirm_task(v.id)
    assert repo.cancel_task(v.id) and not repo.cancel_task(v.id)
    assert repo.get_task(v.id).payload is None

    w = gw.submit(USER, "chat", "второй", provider_id=p.id)
    gw.confirm(USER, w.id)
    t = repo.claim_next()
    while t is not None and t.id != w.id:
        repo.finish_error(t.id, "failed", "test", "x")
        t = repo.claim_next()
    assert t.status == "running" and t.attempts == 1
    assert repo.finish_error(w.id, "timeout", "timeout", "Нет ответа за 120 с")
    assert not repo.finish_ok(w.id, t.payload)  # уже не running
    assert repo.requeue_task(w.id)
    assert repo.get_task(w.id).status == QUEUED


def test_stale_running_marked_failed_and_purge(env):
    pool, repo, _, gw, svc, _ = env
    p = _ext(svc, timeout_sec=1)
    v = gw.submit(USER, "chat", "запрос", provider_id=p.id)
    with pool.connection() as c:
        c.execute("UPDATE app.llm_task SET status = 'running', "
                  "started_at = now() - interval '5 minutes', confirmed_at = now() "
                  "WHERE id = %s::uuid", (v.id,))
    assert repo.fail_stale_running(grace_sec=30) >= 1
    t = repo.get_task(v.id)
    assert t.status == "failed" and t.error_code == "worker_lost"
    with pool.connection() as c:
        c.execute("UPDATE app.llm_task SET finished_at = now() - interval '10 days' "
                  "WHERE id = %s::uuid", (v.id,))
    assert repo.purge_finished(7) >= 1
    assert repo.get_task(v.id) is None


def test_provider_with_history_cannot_be_deleted(env):
    _, _, _, gw, svc, _ = env
    p = _ext(svc)
    gw.submit(USER, "chat", "запрос", provider_id=p.id)
    with pytest.raises(ProviderError) as ei:
        svc.delete(p.id)
    assert ei.value.code == "in_use"


def test_dictionary_sources_and_custom_dictionary(env):
    _, repo, _, _, _, _ = env
    src = DictionarySource(repo)
    assert COMPANY in src.load().companies  # клиент из кэша эпиков Jira
    from app.llm_gateway import Dictionaries

    src.save_custom(Dictionaries(projects=("Проект-Тест-37",), persons=("Тестов Т. Т.",)))
    got = src.load_custom()
    assert got.projects == ("Проект-Тест-37",)
    assert "Проект-Тест-37" in src.load().projects
