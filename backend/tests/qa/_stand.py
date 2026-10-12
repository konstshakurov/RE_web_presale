"""Доступ QA-тестов к живому стенду (KON-57): параметры и HTTP-клиенты.

Параметры берутся из окружения либо из ``qa/stand/.qa.env`` (создаётся ``qa/stand/stand.sh up``,
вне Git). В репозитории значений нет. Без стенда тесты пропускаются (``requires_stand``).
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT / "qa" / "stand" / ".qa.env"
COMPOSE_PROJECT = "re_web_presale_qa"
DENIED_TEXT = "Неверный логин или пароль, либо нет доступа"
LDAP_DOWN_TEXT = "Сервис входа временно недоступен, повторите позже"


def _load() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip()
    values.update({k: v for k, v in os.environ.items() if k.startswith(("QA_", "APP_", "CV_", "JIRA_"))})
    return values


CFG = _load()
BASE_URL = CFG.get("QA_BASE_URL", "")


def stand_available() -> bool:
    if not BASE_URL or not CFG.get("QA_LDAP_PW_ADMIN"):
        return False
    try:
        return httpx.get(BASE_URL + "/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


requires_stand = pytest.mark.skipif(
    not stand_available(), reason="стенд не поднят: bash qa/stand/stand.sh up"
)

PASSWORDS = {
    "qa-admin": CFG.get("QA_LDAP_PW_ADMIN", ""),
    "qa-user": CFG.get("QA_LDAP_PW_USER", ""),
    "qa-outsider": CFG.get("QA_LDAP_PW_OUTSIDER", ""),
    "qa-revoked": CFG.get("QA_LDAP_PW_REVOKED", ""),
    "qa-jirablock": CFG.get("QA_LDAP_PW_JIRABLOCK", ""),
}


class Session:
    """Клиент одного пользователя: cookie сессии хранится в httpx.Client."""

    def __init__(self) -> None:
        self.http = httpx.Client(base_url=BASE_URL + "/api", timeout=15, follow_redirects=False)

    def close(self) -> None:
        self.http.close()

    def login(self, login: str, password: str | None = None) -> httpx.Response:
        pw = PASSWORDS[login] if password is None else password
        return self.http.post("/auth/login", json={"login": login, "password": pw})

    def me(self) -> httpx.Response:
        return self.http.get("/auth/me")

    def csrf(self) -> dict[str, str]:
        r = self.me()
        assert r.status_code == 200, r.text
        return {"X-CSRF-Token": r.json()["csrf_token"]}

    def post(self, url: str, **kw) -> httpx.Response:
        return self.http.post(url, headers=self.csrf(), **kw)

    def patch(self, url: str, **kw) -> httpx.Response:
        return self.http.patch(url, headers=self.csrf(), **kw)

    def users(self) -> dict[str, dict]:
        r = self.http.get("/admin/users")
        assert r.status_code == 200, r.text
        return {u["login"]: u for u in r.json()}


def logged_in(login: str) -> Session:
    s = Session()
    r = s.login(login)
    assert r.status_code == 200, f"{login}: вход не удался, {r.status_code}"
    return s
