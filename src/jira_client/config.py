# -*- coding: utf-8 -*-
"""Конфигурация клиента Jira из переменных окружения.

Секретов в коде нет и быть не должно: клиент читает только ``os.environ``
(плюс необязательный файл ``.env``, который уже исключён из git).
Значения никогда не логируются — вместо них в логи идут только признаки
«задано / не задано» и длина.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import Dict, Mapping, MutableMapping, Optional

#: NFR-009: не более 10 с на один вызов Jira.
DEFAULT_TIMEOUT = 10.0
#: Разумный «потолок», чтобы конфиг не отключил NFR-009 по недосмотру.
MAX_TIMEOUT = 10.0

DEFAULT_BASE_URL = "https://jira.simbirsoft.com"
#: Имя сессионной cookie Jira Server 8.13.0. Переопределяется через env.
DEFAULT_SESSION_COOKIE = "JSESSIONID"

#: Допустимые режимы аутентификации.
AUTH_MODE_COOKIE = "cookie"
AUTH_MODE_BASIC = "basic"
AUTH_MODES = (AUTH_MODE_COOKIE, AUTH_MODE_BASIC)


class ConfigError(ValueError):
    """Некорректный или неполный конфиг. Текст не содержит значений секретов."""


def load_dotenv(path: str, environ: Optional[MutableMapping[str, str]] = None) -> bool:
    """Прочитать ``KEY=value`` из файла в ``environ``. Без внешних зависимостей.

    Возвращает ``True``, если файл был прочитан. Отсутствие файла — не ошибка:
    значения могут прийти из настоящего окружения процесса.
    """
    target = environ if environ is not None else os.environ
    if not path or not os.path.isfile(path):
        return False
    with io.open(path, encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in target:
                target[key] = value
    return True


def _as_bool(raw: Optional[str], default: bool = True) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y", "да")


def _as_float(raw: Optional[str], default: float) -> float:
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError("JIRA_TIMEOUT должен быть числом") from exc
    if value <= 0:
        raise ConfigError("JIRA_TIMEOUT должен быть положительным")
    return value


@dataclass(frozen=True, repr=False)
class JiraConfig:
    """Параметры подключения к Jira. Секреты присутствуют только в памяти."""

    base_url: str
    auth_mode: str = AUTH_MODE_COOKIE
    session_token: Optional[str] = None
    session_cookie: str = DEFAULT_SESSION_COOKIE
    username: Optional[str] = None
    password: Optional[str] = None
    token_encryption_key: Optional[str] = None
    timeout: float = DEFAULT_TIMEOUT
    verify_tls: bool = True
    extra: Dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    # -- проверки -----------------------------------------------------------

    @property
    def has_session_token(self) -> bool:
        return bool(self.session_token)

    @property
    def has_basic_credentials(self) -> bool:
        return bool(self.username and self.password)

    @property
    def has_encryption_key(self) -> bool:
        return bool(self.token_encryption_key)

    def cookie_header_value(self) -> str:
        """Значение заголовка ``Cookie``. Токен наружу не возвращается по имени."""
        return "%s=%s" % (self.session_cookie, self.session_token or "")

    def validate(self) -> "JiraConfig":
        """Проверить, что выбранного режима достаточно для работы."""
        if self.auth_mode == AUTH_MODE_COOKIE and not self.has_session_token:
            raise ConfigError(
                "JIRA_AUTH_MODE=cookie требует JIRA_SESSION_TOKEN; "
                "получите его в приложении и храните зашифрованным (NFR-006)"
            )
        if self.auth_mode == AUTH_MODE_BASIC and not self.has_basic_credentials:
            raise ConfigError("JIRA_AUTH_MODE=basic требует JIRA_USERNAME и JIRA_PASSWORD")
        return self

    def safe_summary(self) -> Dict[str, object]:
        """Описание для логов и отчётов: только факты, никаких значений."""
        return {
            "base_url": self.base_url,
            "auth_mode": self.auth_mode,
            "session_cookie": self.session_cookie,
            "session_token_set": self.has_session_token,
            "basic_credentials_set": self.has_basic_credentials,
            "encryption_key_set": self.has_encryption_key,
            "timeout": self.timeout,
            "verify_tls": self.verify_tls,
        }

    def __repr__(self) -> str:
        """Сокрытие секретов: сгенерированный ``repr`` dataclass показывал бы
        ``session_token`` и ``password`` целиком (критерий Dev-18, NFR-006)."""
        return "JiraConfig(%s)" % ", ".join(
            "%s=%r" % (key, value) for key, value in self.safe_summary().items()
        )

    # -- сборка из окружения ------------------------------------------------

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        *,
        env_file: Optional[str] = None,
        dotenv_path: Optional[str] = None,
    ) -> "JiraConfig":
        """Собрать конфиг из окружения.

        ``env`` — словарь (для тестов). ``env_file``/``dotenv_path`` — путь к
        файлу ``.env``, который будет подгружен, если его ещё нет в окружении.
        """
        source: MutableMapping[str, str] = os.environ if env is None else dict(env)

        path = env_file or dotenv_path
        if path:
            # Загружаем в копию, чтобы не мутировать os.environ целиком.
            sandbox: Dict[str, str] = {}
            load_dotenv(path, sandbox)
            for key, value in sandbox.items():
                source.setdefault(key, value)

        base_url = (source.get("JIRA_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError("JIRA_BASE_URL должен начинаться с http:// или https://")

        mode = (source.get("JIRA_AUTH_MODE") or AUTH_MODE_COOKIE).strip().lower()
        if mode not in AUTH_MODES:
            raise ConfigError(
                "JIRA_AUTH_MODE должен быть одним из: %s" % ", ".join(AUTH_MODES)
            )

        timeout = _as_float(source.get("JIRA_TIMEOUT"), DEFAULT_TIMEOUT)
        if timeout > MAX_TIMEOUT:
            # NFR-009 жёсткий: молча уменьшаем, чтобы не нарушить его из конфига.
            timeout = MAX_TIMEOUT

        return cls(
            base_url=base_url,
            auth_mode=mode,
            session_token=(source.get("JIRA_SESSION_TOKEN") or "").strip() or None,
            session_cookie=(
                (source.get("JIRA_SESSION_COOKIE") or DEFAULT_SESSION_COOKIE).strip()
                or DEFAULT_SESSION_COOKIE
            ),
            username=(source.get("JIRA_USERNAME") or "").strip() or None,
            password=(source.get("JIRA_PASSWORD") or "").strip() or None,
            token_encryption_key=(source.get("JIRA_TOKEN_ENCRYPTION_KEY") or "").strip() or None,
            timeout=timeout,
            verify_tls=_as_bool(source.get("JIRA_VERIFY_TLS"), True),
        )

    @classmethod
    def from_env_file(cls, path: str) -> "JiraConfig":
        return cls.from_env({}, env_file=path)


def describe_env(key: str) -> str:
    """Текст для лога о наличии переменной — без её значения."""
    return "%s=%s" % (key, "задано" if os.environ.get(key) else "НЕ ЗАДАНО")