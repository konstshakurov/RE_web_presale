# -*- coding: utf-8 -*-
"""Аутентификация: сессионная cookie, Basic и заблокированный обмен пароля."""

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
    """Обмен пароля на сессию заблокирован: UC-005 и NFR-006 запрещают это."""

    def test_disabled_by_default(self):
        with pytest.raises(PermissionError) as exc:
            SessionLoginProvider(base_url="https://jira.example.internal")
        assert "UC-005" in str(exc.value)

    def test_body_shape_confirmed_by_probe(self):
        """Форма ``{"username", "password"}`` подтверждена пробой.

        Вложенный объект ``{"session": {...}}`` Jira 8.13.0 отклоняет:
        400 «Unrecognized field "session" (Class
        com.atlassian.jira.rest.auth.AuthParams)».
        """
        provider = SessionLoginProvider(
            base_url="https://jira.example.internal", enabled=True
        )
        assert provider.request_body("u", "p") == {"username": "u", "password": "p"}

    def test_request_body_requires_both_parts(self):
        provider = SessionLoginProvider(
            base_url="https://jira.example.internal", enabled=True
        )
        with pytest.raises(ValueError):
            provider.request_body("u", "")

    def test_endpoint_is_rest_auth_session(self):
        provider = SessionLoginProvider(
            base_url="https://jira.example.internal", enabled=True
        )
        assert provider.endpoint == "/rest/auth/1/session"