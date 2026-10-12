"""Начальный провайдер и конфиг LLM (KON-37): Qwen по умолчанию, env, секреты вне логов."""
from __future__ import annotations

import base64
import os

from app.config import load_settings
from app.llm_gateway import ensure_default_provider
from app.llm_gateway.service import DEFAULT_QWEN_URL

from ._llm_fakes import FakeLlmRepo, MemSecretRepo

ENV = {"APP_DB_URL": "postgresql://x", "APP_ENC_KEY": base64.b64encode(os.urandom(32)).decode()}


def test_default_provider_created_once_from_env():
    s = load_settings({**ENV, "LLM_BASE_URL": "http://qwen.test/api/v1", "LLM_MODEL": "qwen-test"})
    repo = FakeLlmRepo(MemSecretRepo())
    assert ensure_default_provider(repo, s) is True
    p = repo.get_default_provider()
    assert p.url == "http://qwen.test/api/v1" and p.model == "qwen-test"
    assert p.is_external is False and p.timeout_sec == 120 and not p.has_key
    assert ensure_default_provider(repo, s) is False  # идемпотентно
    assert repo.count_providers() == 1


def test_default_url_from_architecture_when_env_empty():
    repo = FakeLlmRepo(MemSecretRepo())
    ensure_default_provider(repo, load_settings(ENV))
    assert repo.get_default_provider().url == DEFAULT_QWEN_URL


def test_llm_api_key_is_secret_and_filtered_from_logs():
    s = load_settings({**ENV, "LLM_API_KEY": "test-env-llm-key-7777"})
    assert "test-env-llm-key-7777" not in repr(s) and "test-env-llm-key-7777" not in str(s)
    assert "test-env-llm-key-7777" in s.secret_values()
    assert s.llm_task_ttl_days == 7


def test_ttl_validation():
    import pytest

    from app.config import ConfigError

    assert load_settings({**ENV, "LLM_TASK_TTL_DAYS": "30"}).llm_task_ttl_days == 30
    with pytest.raises(ConfigError):
        load_settings({**ENV, "LLM_TASK_TTL_DAYS": "0"})
