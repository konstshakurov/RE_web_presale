# -*- coding: utf-8 -*-
"""Аутентификация: сессионная cookie, Basic и вход по паролю (Dev-18)."""

from __future__ import annotations

import base64

import pytest

from jira_client.auth import (
    AnonymousAuth,
    BasicAuth,
    SessionLoginProvider,
    SessionTokenAuth,
    build_auth,
)


def test_session_token_auth_sets_cookie_header():
    auth = SessionTokenAuth("токен-значение")
    assert auth.headers() == {"Cookie": "JSESSIONID=токен-значение"}


def test_session_token_auth_honours_cookie_name():
    auth = SessionTokenAuth("abc", "atl_token")
    assert auth.headers()["Cookie"] == "atl_token=abc"


def test_empty_token_is_rejected():
    with pytest.raises(ValueError):
        SessionTokenAuth("")


def test_repr_does_not_leak_token():
    """NFR-006: токены не должны попадать в логи."""
    auth = SessionTokenAuth("очень-секретный-токен")
    assert "очень-секретный-токен" not in repr(auth)
    assert "очень-секретный-токен" not in str(auth.describe())
    assert auth.describe()["cookie"] == "JSESSIONID"


def test_basic_auth_encodes_header():
    auth = BasicAuth("user", "pass")
    encoded = auth.headers()["Authorization"].split(" ", 1)[1]
    assert base64.b64decode(encoded).decode() == "user:pass"


def test_basic_auth_requires_both_parts():
    with pytest.raises(ValueError):
        BasicAuth("user", "")
    with pytest.raises(ValueError):
        BasicAuth("", "pass")


def test_basic_auth_repr_hides_password():
    auth = BasicAuth("user", "очень-секретный-пароль")
    assert "очень-секретный-пароль" not in repr(auth)
    assert "очень-секретный-пароль" not in str(auth.describe())


def test_anonymous_auth_has_no_headers():
    assert AnonymousAuth().headers() == {}


def test_build_auth_dispatch():
    assert isinstance(build_auth(auth_mode="cookie", session_token="t"), SessionTokenAuth)
    assert isinstance(
        build_auth(auth_mode="basic", username="u", password="p"), BasicAuth
    )
    assert isinstance(build_auth(auth_mode="unknown"), AnonymousAuth)


def test_build_auth_cookie_mode_custom_name():
    auth = build_auth(auth_mode="cookie", session_token="t", session_cookie="atl_token")
    assert auth.headers()["Cookie"] == "atl_token=t"


class TestSessionLoginProvider:
    """Обмен пароля на сессию включён решением владельца 05.10 (ADR-005).

    Ограничение, которое осталось, — пароль живёт только в теле одного
    запроса и нигде не сохраняется (NFR-006).
    """

    BASE = "https://jira.example.internal"

    def test_login_is_available_without_any_flag(self):
        """Флаг ``enabled`` убран: вход — явная работающая операция (Dev-18)."""
        provider = SessionLoginProvider(base_url=self.BASE)
        assert callable(provider.login)
        assert provider.cookie_name == "JSESSIONID"

    def test_missing_base_url_is_rejected(self):
        with pytest.raises(ValueError):
            SessionLoginProvider(base_url="")

    def test_body_shape_confirmed_by_probe(self):
        """Форма ``{"username", "password"}`` подтверждена пробой.

        Вложенный объект ``{"session": {...}}`` Jira 8.13.0 отклоняет:
        400 «Unrecognized field "session" (Class
        com.atlassian.jira.rest.auth.AuthParams)».
        """
        provider = SessionLoginProvider(base_url=self.BASE)
        assert provider.request_body("u", "p") == {"username": "u", "password": "p"}

    def test_request_body_requires_both_parts(self):
        provider = SessionLoginProvider(base_url=self.BASE)
        with pytest.raises(ValueError):
            provider.request_body("u", "")

    def test_endpoint_is_rest_auth_session(self):
        provider = SessionLoginProvider(base_url=self.BASE)
        assert provider.endpoint == "/rest/auth/1/session"

    def test_describe_has_no_secret_keys(self):
        """Безопасное описание для логов: ни паролей, ни токенов."""
        provider = SessionLoginProvider(base_url=self.BASE)
        described = provider.describe()
        assert "password" not in str(described)
        assert "token" not in str(described)
        assert described["endpoint"] == "/rest/auth/1/session"

    def test_repr_has_no_secret_keys(self):
        provider = SessionLoginProvider(base_url=self.BASE)
        assert "password" not in repr(provider)
        assert "token" not in repr(provider)


class TestSessionToken:
    """``SessionToken`` — результат входа; значение cookie является секретом."""

    TOKEN = "секретное-значение-cookie"

    def _token(self):
        from datetime import datetime, timezone

        from jira_client.auth import SessionToken

        return SessionToken(
            cookie_name="JSESSIONID",
            token=self.TOKEN,
            created_at=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc),
            username="пользователь",
        )

    def test_repr_hides_token(self):
        assert self.TOKEN not in repr(self._token())

    def test_describe_hides_token_and_username(self):
        token = self._token()
        described = str(token.describe())
        assert self.TOKEN not in described
        assert "пользователь" not in described
        assert token.describe()["token_set"] is True
        assert token.describe()["username_set"] is True

    def test_created_at_is_reported(self):
        token = self._token()
        assert token.describe()["created_at"].startswith("2026-10-06T12:00")

    def test_cookie_header_value_is_usable(self):
        assert self._token().cookie_header_value() == "JSESSIONID=" + self.TOKEN
