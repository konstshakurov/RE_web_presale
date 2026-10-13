"""422 не возвращает введённые значения (KON-68, NFR-006) и SASLprep-отказ = 401-путь (KON-67)."""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from ldap3.core.exceptions import LDAPSASLPrepError
from pydantic import BaseModel, Field

from app.auth import router
from app.auth.ldap_adapter import Ldap3Adapter
from app.validation import install_validation_handler, reject_nul, sanitize_errors

SECRET = "FAKE_Pw-Zq9!unit-secret-4417"


class Body(BaseModel):
    note: str = Field(max_length=5)
    count: int


def _client() -> TestClient:
    app = FastAPI()
    install_validation_handler(app)
    app.include_router(router)

    @app.post("/echo")
    def echo(body: Body):
        return {}

    return TestClient(app)


def test_login_overlong_password_not_echoed():
    r = _client().post("/auth/login", json={"login": "ivan", "password": SECRET + "p" * 1100})
    assert r.status_code == 422
    assert SECRET not in r.text and "ppppp" not in r.text
    err = r.json()["detail"][0]
    assert "input" not in err
    assert err["loc"] == ["body", "password"] and err["type"] == "string_too_long"
    assert err["msg"] and err["ctx"] == {"max_length": 1024}


def test_login_wrong_type_password_not_echoed():
    r = _client().post("/auth/login", json={"login": "ivan", "password": [SECRET]})
    assert r.status_code == 422
    assert SECRET not in r.text
    assert all("input" not in e for e in r.json()["detail"])
    assert r.json()["detail"][0]["loc"] == ["body", "password"]


def test_missing_field_does_not_echo_whole_body():
    r = _client().post("/auth/login", json={"password": SECRET})
    assert r.status_code == 422 and SECRET not in r.text
    assert all("input" not in e for e in r.json()["detail"])


def test_invalid_json_not_echoed():
    r = _client().post(
        "/auth/login", content=('{"password": "%s"' % SECRET).encode(),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422 and SECRET not in r.text


def test_other_endpoints_covered_and_format_kept():
    r = _client().post("/echo", json={"note": SECRET, "count": SECRET})
    assert r.status_code == 422 and SECRET not in r.text
    detail = r.json()["detail"]
    assert {tuple(e["loc"]) for e in detail} == {("body", "note"), ("body", "count")}
    assert all({"type", "loc", "msg"} <= e.keys() and "input" not in e for e in detail)


def test_sanitize_drops_non_numeric_ctx():
    out = sanitize_errors([
        {"type": "t", "loc": ("q",), "msg": "m", "input": SECRET,
         "ctx": {"error": ValueError(SECRET), "expected": SECRET, "gt": 3}},
    ])
    assert out == [{"type": "t", "loc": ("q",), "msg": "m", "ctx": {"gt": 3}}]


def test_main_app_registers_handler():
    from fastapi.exceptions import RequestValidationError

    from app.main import app

    assert RequestValidationError in app.exception_handlers


def test_saslprep_error_on_bind_is_denied_not_unavailable(monkeypatch):
    """KON-67: управляющий символ в пароле - отказ (None), а не LdapUnavailable/503."""
    def boom(self):
        raise LDAPSASLPrepError("prohibited character")

    monkeypatch.setattr("app.auth.ldap_adapter.Connection.bind", boom)
    ad = Ldap3Adapter("ldap://127.0.0.1:1", "", "", bind_template="uid={login},dc=x")
    for pw in ("\t", "a\nb", "\x01", "\x7f"):
        assert ad.authenticate_profile("ivan", pw) is None


@pytest.mark.parametrize("pw", ["\t", "\x01"])
def test_control_char_password_via_api_is_401(pw, monkeypatch):
    # через общий слой: LdapUnavailable не возникает, ответ - обычный отказ
    from app.auth.ldap_port import LdapUnavailable  # noqa: F401 - порт тот же, что в адаптере

    def boom(self):
        raise LDAPSASLPrepError("prohibited character")

    monkeypatch.setattr("app.auth.ldap_adapter.Connection.bind", boom)
    ad = Ldap3Adapter("ldap://127.0.0.1:1", "", "", bind_template="uid={login},dc=x")
    assert ad.authenticate("ivan", pw) is False


def _nul_client() -> TestClient:
    app = FastAPI(dependencies=[Depends(reject_nul)])
    install_validation_handler(app)

    @app.get("/list")
    def lst(q: str | None = None, status: str | None = None):
        return {"q": q}

    @app.get("/item/{key}")
    def item(key: str):
        return {"key": key}

    @app.post("/body")
    def body(payload: dict):
        return {}

    return TestClient(app)


@pytest.mark.parametrize("param", ["q", "status"])
def test_nul_in_query_is_422_without_echo(param):
    r = _nul_client().get(f"/list?{param}=a%00bSECRETX")
    assert r.status_code == 422
    assert "SECRETX" not in r.text and "input" not in r.text
    assert r.json()["detail"][0]["loc"] == ["query", param]


def test_nul_in_repeated_query_param_is_422():
    r = _nul_client().get("/list?q=ok&q=a%00b")
    assert r.status_code == 422


def test_nul_in_path_is_422():
    r = _nul_client().get("/item/a%00b")
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["path", "key"]


def test_nul_in_json_body_is_422():
    c = _nul_client()
    r = c.post("/body", content=r'{"query": ["x", {"deep": "a\u0000b"}]}',
               headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "query", 1, "deep"]


def test_clean_values_and_control_chars_pass():
    c = _nul_client()
    assert c.get("/list", params={"q": "ab", "status": "Требуется кандидат"}).status_code == 200
    r = c.post("/body", json={"q": r"text \u0000 literal backslash"})
    assert r.status_code == 200
    bad = c.post("/body", content="{bad", headers={"content-type": "application/json"})
    assert bad.status_code == 422


def test_main_app_has_nul_dependency():
    from app.main import app

    assert any(d.dependency is reject_nul for d in app.router.dependencies)
