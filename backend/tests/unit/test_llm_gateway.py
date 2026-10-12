"""Шлюз LLM (ADR-007): внутренний без обезличивания, внешний - предпросмотр и подтверждение,
таймаут (NFR-010), повтор, обратная подстановка, секреты (NFR-006). БД и сеть не нужны."""
from __future__ import annotations

import json
import logging
import time

import pytest

from app.llm_gateway import Dictionaries, GatewayError
from app.llm_gateway.anonymize import AnonymizationError, Anonymizer
from app.llm_gateway.client import LlmCallError, LlmTimeoutError
from app.llm_gateway.repo import AWAITING, CANCELLED, DONE, FAILED, QUEUED, TIMEOUT

from ._llm_fakes import FAKE_PROVIDER_KEY, Env, FakeClient, FakeNer

COMPANY = "Тестовая Компания Альфа"
PERSON = "Тестов Тест Тестович"
PROMPT = f"Подобрать кандидата для {COMPANY}. Кандидат {PERSON}, почта tester@example.test."


@pytest.fixture()
def env():
    e = Env()
    e.repo.sources["companies"] = [COMPANY]
    e.repo.sources["persons"] = [PERSON]
    return e


def test_internal_provider_not_anonymized_and_queued(env):
    v = env.gateway.submit("ivan", "match", PROMPT)
    assert v.status == QUEUED and not v.is_external and not v.requires_confirmation
    assert v.preview is None  # предпросмотр только для внешнего
    env.gateway.run_next()
    call = env.client.calls[0]
    assert call["prompt"] == PROMPT and call["url"].startswith("http://qwen.test")
    assert call["model"] == "qwen-test" and call["timeout_sec"] == 120
    done = env.gateway.get("ivan", v.id)
    assert done.status == DONE and done.result == "ответ: " + PROMPT


def test_external_requires_confirmation_before_any_send(env):
    v = env.gateway.submit("ivan", "match", PROMPT, provider_id=env.external.id)
    assert v.status == AWAITING and v.requires_confirmation and v.is_external
    # без подтверждения worker ничего не берёт и ничего не отправляет
    assert env.gateway.run_next() is None
    assert env.client.calls == []
    env.gateway.confirm("ivan", v.id)
    assert env.gateway.run_next() is not None
    assert len(env.client.calls) == 1


def test_external_preview_is_exactly_what_is_sent_and_has_no_pd(env):
    v = env.gateway.submit("ivan", "match", PROMPT, provider_id=env.external.id,
                           system=f"Ты помощник {COMPANY}.")
    for secret in (COMPANY, PERSON, "tester@example.test"):
        assert secret not in v.preview.prompt and secret not in (v.preview.system or "")
    assert "[КОМПАНИЯ_1]" in v.preview.prompt and "[ПЕРСОНА_1]" in v.preview.prompt
    assert v.preview.replacements == {"company": 1, "person": 1, "email": 1}
    env.gateway.confirm("ivan", v.id)
    env.gateway.run_next()
    sent = env.client.calls[0]
    assert sent["prompt"] == v.preview.prompt and sent["system"] == v.preview.system
    assert sent["api_key"] == FAKE_PROVIDER_KEY and sent["model"] == "ext-model"
    assert sent["url"] == "https://llm.example.test/v1"
    for secret in (COMPANY, PERSON, "tester@example.test"):
        assert secret not in json.dumps(sent, ensure_ascii=False).replace(FAKE_PROVIDER_KEY, "")


def test_result_gets_placeholders_restored(env):
    env.client = FakeClient(lambda p: "Рекомендую [ПЕРСОНА_1] для [КОМПАНИЯ_1]; пишите [EMAIL_1]")
    env.gateway._client = env.client
    v = env.gateway.submit("ivan", "match", PROMPT, provider_id=env.external.id)
    env.gateway.confirm("ivan", v.id)
    env.gateway.run_next()
    done = env.gateway.get("ivan", v.id)
    assert done.status == DONE
    assert done.result == f"Рекомендую {PERSON} для {COMPANY}; пишите tester@example.test"
    assert done.preview is None  # после завершения подстановки и текст запроса очищены


def test_nothing_plain_in_db_rows(env):
    v = env.gateway.submit("ivan", "match", PROMPT, provider_id=env.external.id)
    row = env.repo.tasks[v.id]
    blob = row["payload"].ciphertext
    for secret in (COMPANY, PERSON, "tester", "[КОМПАНИЯ_1]"):
        assert secret.encode() not in blob
    env.gateway.confirm("ivan", v.id)
    env.gateway.run_next()
    row = env.repo.tasks[v.id]
    assert row["payload"] is None
    assert COMPANY.encode() not in row["result"].ciphertext


def test_internal_prompt_stored_encrypted_too(env):
    v = env.gateway.submit("ivan", "chat", PROMPT)
    assert PERSON.encode() not in env.repo.tasks[v.id]["payload"].ciphertext


def test_timeout_message_and_retry(env):
    # провайдер отвечает дольше таймаута из настроек: 1 с (NFR-010)
    env.providers.update(env.internal.id, timeout_sec=1)
    env.gateway._client = env.client = FakeClient(delay=2.5)
    v = env.gateway.submit("ivan", "match", "обычный запрос")
    t0 = time.monotonic()
    env.gateway.run_next()
    assert time.monotonic() - t0 < 2.0  # не ждём зависший вызов
    t = env.gateway.get("ivan", v.id)
    assert t.status == TIMEOUT and t.error_code == "timeout"
    assert "1 с" in t.error_message and "Повторите" in t.error_message
    assert env.client.calls[0]["timeout_sec"] == 1.0
    # повтор: тот же запрос уходит снова и завершается
    env.gateway._client = FakeClient()
    r = env.gateway.retry("ivan", v.id)
    assert r.status == QUEUED
    env.gateway.run_next()
    assert env.gateway.get("ivan", v.id).status == DONE


def test_client_timeout_error_maps_to_timeout_status(env):
    env.gateway._client = FakeClient(error=LlmTimeoutError(120))
    v = env.gateway.submit("ivan", "match", "запрос")
    env.gateway.run_next()
    t = env.gateway.get("ivan", v.id)
    assert t.status == TIMEOUT and "120 с" in t.error_message


def test_default_timeout_is_120_and_configurable(env):
    assert env.internal.timeout_sec == 120
    env.providers.update(env.internal.id, timeout_sec=45)
    v = env.gateway.submit("ivan", "match", "запрос")
    assert v.timeout_sec == 45
    env.gateway.run_next()
    assert env.client.calls[0]["timeout_sec"] == 45.0


def test_provider_error_is_failed_and_retryable(env):
    env.gateway._client = FakeClient(error=LlmCallError("provider_error", "HTTP 500"))
    v = env.gateway.submit("ivan", "match", "запрос")
    env.gateway.run_next()
    t = env.gateway.get("ivan", v.id)
    assert t.status == FAILED and t.error_code == "provider_error"
    env.gateway.retry("ivan", v.id)
    with pytest.raises(GatewayError) as ei:
        env.gateway.retry("ivan", v.id)  # уже в очереди
    assert ei.value.code == "not_retryable"


def test_external_without_key_fails_before_call(env):
    env.providers.update(env.external.id, remove_key=False)
    env.secrets.llm.clear()  # ключ потерян/удалён вне API
    v = env.gateway.submit("ivan", "match", "запрос", provider_id=env.external.id)
    env.gateway.confirm("ivan", v.id)
    env.gateway.run_next()
    t = env.gateway.get("ivan", v.id)
    assert t.status == FAILED and t.error_code == "no_key" and env.client.calls == []


def test_internal_uses_env_key_when_none_stored():
    e = Env(env_key="env-test-key-1111")
    e.gateway.submit("ivan", "match", "запрос")
    e.gateway.run_next()
    assert e.client.calls[0]["api_key"] == "env-test-key-1111"


def test_external_never_uses_env_key():
    e = Env(env_key="env-test-key-1111")
    e.secrets.llm.clear()
    v = e.gateway.submit("ivan", "match", "запрос", provider_id=e.external.id)
    e.gateway.confirm("ivan", v.id)
    e.gateway.run_next()
    assert e.gateway.get("ivan", v.id).error_code == "no_key"


def test_no_model_configured(env):
    env.providers.update(env.internal.id, model="")
    v = env.gateway.submit("ivan", "match", "запрос")
    env.gateway.run_next()
    assert env.gateway.get("ivan", v.id).error_code == "no_model"


def test_unconfirmed_external_task_is_not_sent_even_if_queued(env):
    """Защита в глубину: даже если задачу кто-то поставил в queued мимо confirm."""
    v = env.gateway.submit("ivan", "match", PROMPT, provider_id=env.external.id)
    env.repo.tasks[v.id]["status"] = QUEUED  # confirmed_at остаётся пустым
    env.gateway.run_next()
    assert env.client.calls == []
    assert env.gateway.get("ivan", v.id).error_code == "not_confirmed"


def test_leak_guard_blocks_send(env, monkeypatch):
    """Если после обезличивания в тексте опознаётся ПДн - задача не создаётся."""
    monkeypatch.setattr(Anonymizer, "find_leaks", lambda self, text: ["остаток"])
    with pytest.raises(AnonymizationError):
        env.gateway.submit("ivan", "match", PROMPT, provider_id=env.external.id)
    assert env.repo.tasks == {}


def test_extra_terms_for_single_request(env):
    v = env.gateway.submit(
        "ivan", "match", "Опыт в проекте Сигма-Тест.", provider_id=env.external.id,
        terms=Dictionaries(projects=("Сигма-Тест",)))
    assert "Сигма-Тест" not in v.preview.prompt


def test_custom_admin_dictionary_applied(env):
    env.dictionary.save_custom(Dictionaries(companies=("Дельта-Тест",)))
    v = env.gateway.submit("ivan", "match", "Клиент Дельта-Тест.", provider_id=env.external.id)
    assert v.preview.prompt == "Клиент [КОМПАНИЯ_1]."


def test_ner_used_in_pipeline():
    e = Env(ner=FakeNer([("Незнакомцев Иван", "person")]))
    v = e.gateway.submit("ivan", "match", "Звонил Незнакомцев Иван.", provider_id=e.external.id)
    assert v.preview.prompt == "Звонил [ПЕРСОНА_1]." and v.preview.ner_used


def test_cancel_and_ownership(env):
    v = env.gateway.submit("ivan", "match", PROMPT, provider_id=env.external.id)
    with pytest.raises(GatewayError) as ei:
        env.gateway.get("petr", v.id)
    assert ei.value.code == "task_not_found"
    with pytest.raises(GatewayError):
        env.gateway.confirm("petr", v.id)
    c = env.gateway.cancel("ivan", v.id)
    assert c.status == CANCELLED
    assert env.repo.tasks[v.id]["payload"] is None
    with pytest.raises(GatewayError) as ei:
        env.gateway.confirm("ivan", v.id)  # после отмены подтвердить нельзя
    assert ei.value.code == "not_awaiting_confirmation"
    assert env.gateway.run_next() is None


def test_bad_task_id_and_empty_prompt(env):
    with pytest.raises(GatewayError) as ei:
        env.gateway.get("ivan", "не-uuid")
    assert ei.value.code == "task_not_found"
    with pytest.raises(GatewayError) as ei:
        env.gateway.submit("ivan", "match", "   ")
    assert ei.value.code == "empty_prompt"


def test_unknown_and_missing_default_provider(env):
    with pytest.raises(GatewayError) as ei:
        env.gateway.submit("ivan", "match", "запрос", provider_id=999)
    assert ei.value.code == "provider_not_found"
    for p in env.repo.providers.values():
        p["is_default"] = False
    with pytest.raises(GatewayError) as ei:
        env.gateway.submit("ivan", "match", "запрос")
    assert ei.value.code == "no_default_provider"


def test_fifo_queue(env):
    a = env.gateway.submit("ivan", "match", "первый")
    b = env.gateway.submit("ivan", "match", "второй")
    env.gateway.run_next()
    assert env.gateway.get("ivan", a.id).status == DONE
    assert env.gateway.get("ivan", b.id).status == QUEUED


def test_internal_error_does_not_crash_worker(env):
    class Boom(FakeClient):
        def complete(self, **kw):
            raise RuntimeError("секретный текст запроса не должен попасть в журнал")

    env.gateway._client = Boom()
    v = env.gateway.submit("ivan", "match", "запрос")
    env.gateway.run_next()
    t = env.gateway.get("ivan", v.id)
    assert t.status == FAILED and t.error_code == "internal_error"
    assert "секретный" not in (t.error_message or "")


def test_logs_have_no_texts_or_keys(env, caplog):
    caplog.set_level(logging.DEBUG)
    v = env.gateway.submit("ivan", "match", PROMPT, provider_id=env.external.id)
    env.gateway.confirm("ivan", v.id)
    env.gateway.run_next()
    text = caplog.text
    for secret in (COMPANY, PERSON, "tester@example.test", FAKE_PROVIDER_KEY):
        assert secret not in text


def test_key_roundtrip_encrypted_and_not_in_provider_row(env):
    stored = env.secrets.llm[env.external.id]
    assert FAKE_PROVIDER_KEY.encode() not in stored.ciphertext
    assert env.store.load_llm_key(env.external.id) == FAKE_PROVIDER_KEY
    assert FAKE_PROVIDER_KEY not in repr(env.repo.get_provider(env.external.id))
    assert env.repo.get_provider(env.external.id).has_key is True
