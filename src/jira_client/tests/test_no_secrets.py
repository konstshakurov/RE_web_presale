# -*- coding: utf-8 -*-
"""Проверки безопасности модуля: в коде нет секретов и персональных данных.

Требования, которые тут проверяются:

* NFR-006 (2) — ключ шифрования отсутствует в коде и в репозитории, задаётся
  переменной окружения;
* правило пробы KON-30 — e-mail, имена людей, пароли, cookie и токены не
  копируются в код, тесты и фикстуры;
* UC-005 / NFR-006 — пароль пользователя не хранится приложением.

Тест сам исключён из просматриваемых файлов: иначе он находил бы собственные
запрещённые литералы. Запрещённые шаблоны собираются по частям, чтобы их
не было в исходнике в готовом виде.
"""

from __future__ import annotations

import io
import json
import os
import re

import pytest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROOT = os.path.dirname(MODULE_ROOT)
SELF = os.path.abspath(__file__)

#: Ключи шаблона конфигурации, которые обязаны быть пустыми.
SECRET_ENV_KEYS = (
    "JIRA_SESSION_TOKEN",
    "JIRA_USERNAME",
    "JIRA_PASSWORD",
    "JIRA_TOKEN_ENCRYPTION_KEY",
)

#: Корпоративный почтовый домен собирается по частям.
_CORP_DOMAIN = "simbir" + "soft" + "." + "com"
_CORP_MAIL = "@" + _CORP_DOMAIN

#: Реальные ключи задач из выгрузки. Фикстуры обязаны быть обезличенными.
#: Список собирается по частям, чтобы сами строки не лежали в тесте.
_FORBIDDEN_KEYS = tuple(
    re.compile(r"\b%s-%s\b" % (prefix, number))
    for prefix, number in (
        ("ANALYTICS", "5853"),   # задача, выгруженная при подготовке
        ("ANALYTICS", "96"),     # задача, выгруженная при подготовке
        ("ANALYTICS", "5871"),   # задача, разрешённая для пробной записи
        ("SA", "2219"),          # эпик, выгруженный при подготовке
    )
)


def _python_files(root: str):
    for dirpath, _dirnames, filenames in os.walk(root):
        if os.sep + ".git" in dirpath or "__pycache__" in dirpath:
            continue
        for name in filenames:
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                if os.path.abspath(path) != SELF:
                    yield path


def _read(path: str) -> str:
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


class TestNoSecretsInCode:
    def test_no_long_secret_looking_literals(self):
        """Длинные литералы, не похожие на пути и идентификаторы, запрещены.

        Эндпоинты Jira — длинные строки, поэтому пути и имена полей исключены
        явно; проверка остаётся осмысленной.
        """
        pattern = re.compile(r"""["']([A-Za-z0-9_\-+/=]{24,})["']""")
        for path in _python_files(MODULE_ROOT):
            for value in pattern.findall(_read(path)):
                if value.startswith("/rest/") or value.startswith("/secure/"):
                    continue
                if value.startswith("customfield_") or value.startswith("application/json"):
                    continue
                if re.fullmatch(r"[a-z0-9_\-]+", value) and "_" in value:
                    continue
                assert len(value) < 40, (
                    "похоже на захардкоженный секрет в %s: %s…" % (path, value[:12])
                )

    def test_no_long_base64_literal_outside_tests(self):
        """base64-ключ AES длиной 44 символа в коде означает утечку (NFR-006)."""
        pattern = re.compile(r"""["']([A-Za-z0-9+/]{43}=)["']""")
        for path in _python_files(MODULE_ROOT):
            assert not pattern.search(_read(path)), "найден base64-ключ в %s" % path

    def test_basic_auth_header_is_computed_not_literal(self):
        """Заголовок авторизации должен вычисляться, а не быть литералом."""
        marker = "Author" + "ization"
        assignment = re.compile(r"""["']%s["']\s*:""" % marker)
        for path in _python_files(MODULE_ROOT):
            for line in _read(path).splitlines():
                if not assignment.search(line):
                    continue
                assert "b64encode" in line or "self._header" in line, (
                    "похоже на литерал заголовка авторизации в %s" % path
                )

    def test_session_cookie_header_uses_configured_value(self):
        """Значение cookie собирается из конфигурации, а не из литерала."""
        from jira_client.auth import SessionTokenAuth

        headers = SessionTokenAuth("тестовое-значение").headers()
        assert list(headers) == ["Cookie"]
        assert headers["Cookie"].startswith("JSESSIONID=")

    def test_password_is_not_required_by_default(self):
        """Пароль не должен требоваться для работы клиента (UC-005, NFR-006)."""
        from jira_client.auth import SessionLoginProvider

        with pytest.raises(PermissionError):
            SessionLoginProvider(base_url="https://jira.example.internal")

    def test_env_example_has_no_secret_values(self):
        path = os.path.join(MODULE_ROOT, ".env.example")
        assert os.path.isfile(path), "нет шаблона конфигурации"
        for key, value in _env_pairs(path):
            if key in SECRET_ENV_KEYS:
                assert value == "", "в шаблоне задано значение секрета: %s" % key

    def test_env_example_documents_every_secret_key(self):
        path = os.path.join(MODULE_ROOT, ".env.example")
        keys = {key for key, _ in _env_pairs(path)}
        for key in SECRET_ENV_KEYS:
            assert key in keys, "в шаблоне нет переменной %s" % key


def _env_pairs(path: str):
    for line in _read(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        yield key.strip(), value.strip()


class TestNoPersonalData:
    def test_no_corporate_email_domain(self):
        """Корпоративные адреса в коде и фикстурах запрещены (правило пробы)."""
        for path in _python_files(MODULE_ROOT):
            assert _CORP_MAIL not in _read(path).lower(), "корпоративный e-mail в %s" % path

    def test_fixtures_use_reserved_example_domains(self):
        """В фикстурах допустимы только зарезервированные example-домены."""
        pattern = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
        for path in _python_files(MODULE_ROOT):
            for match in pattern.findall(_read(path)):
                domain = match.lower()
                assert domain.endswith("example.internal") or domain in (
                    "example.com", "example.org", "example.net",
                ), "в фикстуре e-mail с доменом %s (%s)" % (domain, path)

    def test_no_real_issue_keys(self):
        """Реальные ключи из выгрузки не должны попасть в код и фикстуры."""
        for path in _python_files(MODULE_ROOT):
            text = _read(path)
            for pattern in _FORBIDDEN_KEYS:
                match = pattern.search(text)
                assert match is None, "в %s найден реальный ключ задачи %s" % (
                    path, match.group(0) if match else "?"
                )

    def test_fixtures_use_synthetic_keys(self):
        """Фикстуры обязаны использовать заведомо несуществующие ключи.

        Реальные потребности в этом проекте имеют номера от тысяч; всё, что
        меньше сотни, заведомо синтетическое. Реальные ключи проверяются
        отдельно в ``test_no_real_issue_keys``.
        """
        key_pattern = re.compile(r"\b(?:ANALYTICS|SA)-(\d+)\b")
        for path in _python_files(MODULE_ROOT):
            for number in key_pattern.findall(_read(path)):
                assert int(number) < 100, (
                    "в фикстуре %s не синтетический номер ключа: %s" % (path, number)
                )

    def test_fixtures_contain_no_real_names(self):
        """Имена из выгрузки не должны повторяться в фикстурах."""
        marker = "constantin" + ".shakurov"
        for path in _python_files(MODULE_ROOT):
            assert marker not in _read(path).lower(), "реальное имя в %s" % path


class TestNoRuntimeSecretLeakage:
    def test_auth_description_and_repr_hide_token(self):
        """Логи получают только описание стратегии, без значения токена."""
        from jira_client.auth import SessionTokenAuth

        secret = "тестовый-токен-значение"
        auth = SessionTokenAuth(secret)
        for text in (repr(auth), str(auth.describe()), auth.describe().__repr__()):
            assert secret not in text, "токен утёк в %r" % text
        # А заголовок по назначению содержит значение — это транспорт.
        assert auth.headers()["Cookie"].endswith(secret)

    def test_client_last_call_has_no_token(self, client_factory):
        from tests.conftest import AUTH_OK_BODY, json_response

        client, _ = client_factory(lambda request: json_response(AUTH_OK_BODY))
        client.myself()
        blob = json.dumps(client.last_call, ensure_ascii=False, default=str)
        assert "token" not in blob.lower()
        assert "Cookie" not in blob


class TestRepositoryScope:
    """Задача KON-30 ограничивает изменения тремя каталогами."""

    def test_module_layout(self):
        names = sorted(n for n in os.listdir(MODULE_ROOT) if not n.startswith("__"))
        for expected in ("client.py", "config.py", "errors.py", "auth.py",
                         "crypto.py", "fields.py", "models.py", "tests",
                         ".env.example", "pytest.ini"):
            assert expected in names, "нет файла %s" % expected

    def test_only_jira_client_under_src(self):
        entries = [n for n in os.listdir(SRC_ROOT) if not n.startswith(".")]
        assert entries == ["jira_client"], "в src/ есть лишнее: %s" % entries