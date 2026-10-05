# -*- coding: utf-8 -*-
"""Конфигурация из окружения: NFR-006 (секреты только из env), NFR-009 (10 с)."""

from __future__ import annotations

import os

import pytest

from jira_client.config import (
    AUTH_MODE_BASIC,
    AUTH_MODE_COOKIE,
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    ConfigError,
    JiraConfig,
    load_dotenv,
)


def test_from_env_reads_cookie_mode():
    config = JiraConfig.from_env(
        {
            "JIRA_BASE_URL": "https://jira.example.internal",
            "JIRA_AUTH_MODE": "cookie",
            "JIRA_SESSION_TOKEN": "faketoken",
        }
    )
    assert config.base_url == "https://jira.example.internal"
    assert config.auth_mode == AUTH_MODE_COOKIE
    assert config.session_token == "faketoken"
    assert config.has_session_token is True


def test_base_url_trailing_slash_is_stripped():
    config = JiraConfig.from_env({"JIRA_BASE_URL": "https://jira.example.internal/"})
    assert config.base_url == "https://jira.example.internal"


def test_default_timeout_is_ten_seconds():
    """NFR-009: лимит 10 с на вызов Jira."""
    assert DEFAULT_TIMEOUT == 10.0
    assert JiraConfig.from_env({}).timeout == 10.0


def test_timeout_cannot_exceed_nfr_limit():
    """Конфиг не должен позволить нарушить NFR-009."""
    config = JiraConfig.from_env({"JIRA_TIMEOUT": "120"})
    assert config.timeout == MAX_TIMEOUT == 10.0


def test_timeout_must_be_positive_number():
    with pytest.raises(ConfigError):
        JiraConfig.from_env({"JIRA_TIMEOUT": "0"})
    with pytest.raises(ConfigError):
        JiraConfig.from_env({"JIRA_TIMEOUT": "быстро"})


def test_base_url_scheme_is_validated():
    with pytest.raises(ConfigError):
        JiraConfig.from_env({"JIRA_BASE_URL": "jira.example.internal"})


def test_unknown_auth_mode_is_rejected():
    with pytest.raises(ConfigError) as exc:
        JiraConfig.from_env({"JIRA_AUTH_MODE": "oauth"})
    assert "cookie" in str(exc.value)


def test_validate_requires_token_for_cookie_mode():
    config = JiraConfig.from_env({"JIRA_AUTH_MODE": "cookie"})
    with pytest.raises(ConfigError) as exc:
        config.validate()
    assert "JIRA_SESSION_TOKEN" in str(exc.value)


def test_validate_requires_password_for_basic_mode():
    config = JiraConfig.from_env({"JIRA_AUTH_MODE": "basic", "JIRA_USERNAME": "u"})
    with pytest.raises(ConfigError) as exc:
        config.validate()
    assert "JIRA_PASSWORD" in str(exc.value)


def test_basic_mode_validates_with_both_parts():
    config = JiraConfig.from_env(
        {"JIRA_AUTH_MODE": "basic", "JIRA_USERNAME": "u", "JIRA_PASSWORD": "p"}
    ).validate()
    assert config.has_basic_credentials is True


def test_safe_summary_never_leaks_values():
    """Логи и отчёты получают только признаки «задано / не задано»."""
    config = JiraConfig.from_env(
        {
            "JIRA_SESSION_TOKEN": "секретный-токен-значение",
            "JIRA_PASSWORD": "секретный-пароль",
        }
    )
    summary = config.safe_summary()
    blob = repr(summary)
    assert "секретный-токен-значение" not in blob
    assert "секретный-пароль" not in blob
    assert summary["session_token_set"] is True
    assert summary["basic_credentials_set"] is False


def test_cookie_header_uses_configured_cookie_name():
    config = JiraConfig.from_env(
        {"JIRA_SESSION_TOKEN": "abc", "JIRA_SESSION_COOKIE": "atl_token"}
    )
    assert config.cookie_header_value() == "atl_token=abc"


def test_load_dotenv_parses_quotes_and_comments(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        '# комментарий\nJIRA_BASE_URL="https://jira.example.internal"\n'
        "JIRA_AUTH_MODE=cookie\nJIRA_SESSION_TOKEN='tok'\n\nмусорная строка\n",
        encoding="utf-8",
    )
    env = {}
    assert load_dotenv(str(path), env) is True
    assert env["JIRA_BASE_URL"] == "https://jira.example.internal"
    assert env["JIRA_AUTH_MODE"] == "cookie"
    assert env["JIRA_SESSION_TOKEN"] == "tok"
    assert "мусорная строка" not in env


def test_load_dotenv_does_not_overwrite_existing_values(tmp_path):
    path = tmp_path / ".env"
    path.write_text("JIRA_BASE_URL=https://from-file\n", encoding="utf-8")
    env = {"JIRA_BASE_URL": "https://from-env"}
    load_dotenv(str(path), env)
    assert env["JIRA_BASE_URL"] == "https://from-env"


def test_load_dotenv_missing_file_is_not_an_error():
    assert load_dotenv("/no/such/file/.env", {}) is False


def test_env_file_is_read_when_env_empty():
    config = JiraConfig.from_env_file(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.example")
    )
    # В шаблоне значения пустые, поэтому режим cookie без токена невалиден.
    assert config.base_url.startswith("https://")
    assert config.has_session_token is False


def test_env_example_template_has_no_secret_values():
    """Шаблон обязателен, но реальных значений секретов в нём быть не должно.

    Не-секретные значения (базовый URL, режим, таймаут) в шаблоне нужны и
    допустимы — они не раскрывают ничего.
    """
    from tests.test_no_secrets import SECRET_ENV_KEYS

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.example"
    )
    assert os.path.isfile(path)
    pairs = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            pairs[key.strip()] = value.strip()
    for key in SECRET_ENV_KEYS:
        assert key in pairs, "в шаблоне нет переменной %s" % key
        assert pairs[key] == "", "в шаблоне задано значение секрета %s" % key

    # Не-секретные настройки должны быть заданы, иначе шаблон бесполезен.
    assert pairs["JIRA_BASE_URL"].startswith("https://")
    assert pairs["JIRA_AUTH_MODE"] in ("cookie", "basic")
    assert float(pairs["JIRA_TIMEOUT"]) <= 10.0