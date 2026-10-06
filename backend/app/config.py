"""Типизированный конфиг из env (NFR-006). Секреты в repr и логах скрыты.

Ошибка запуска (``ConfigError``) при пустом или некорректном APP_ENC_KEY / APP_DB_URL.
"""
from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

KEY_BYTES = 32


class ConfigError(RuntimeError):
    """Конфиг неполон или неверен. Значения секретов в текст не попадают."""


class Secret:
    """Обёртка секрета: str/repr скрыты, значение отдаёт только ``reveal()``."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)


def parse_admin_logins(raw: str) -> tuple[str, ...]:
    """Список логинов через запятую/пробел/«;» -> нижний регистр, без дублей, порядок сохранён."""
    seen: dict[str, None] = {}
    for part in raw.replace(";", ",").replace("\n", ",").replace(" ", ",").split(","):
        login = part.strip().lower()
        if login:
            seen.setdefault(login, None)
    return tuple(seen)


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigError("APP_ENC_KEY должен быть корректной base64-строкой") from exc
    if len(key) != KEY_BYTES:
        raise ConfigError(f"APP_ENC_KEY должен декодироваться ровно в {KEY_BYTES} байт")
    return key


@dataclass(frozen=True)
class Settings:
    app_db_url: Secret = field(repr=True)  # в URL есть пароль роли
    enc_key: Secret = field(repr=True)
    enc_key_id: str = "k1"
    admin_logins: tuple[str, ...] = ()
    ldap_url: str = ""
    ldap_base_dn: str = ""
    ldap_user_filter: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: Secret = Secret("")
    ldap_use_tls: bool = False
    jira_base_url: str = ""
    llm_base_url: str = ""

    def enc_key_bytes(self) -> bytes:
        return _decode_key(self.enc_key.reveal())

    def secret_values(self) -> list[str]:
        """Значения секретов для фильтра логов."""
        values = [self.app_db_url, self.enc_key, self.ldap_bind_password]
        return [s.reveal() for s in values if s]


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Прочитать конфиг. ``env`` по умолчанию ``os.environ``."""
    e = os.environ if env is None else env

    def get(name: str, default: str = "") -> str:
        return (e.get(name) or default).strip()

    missing = [n for n in ("APP_DB_URL", "APP_ENC_KEY") if not get(n)]
    if missing:
        raise ConfigError("Не заданы обязательные переменные окружения: " + ", ".join(missing))

    key_id = get("APP_ENC_KEY_ID", "k1")
    if not 1 <= len(key_id) <= 64:
        raise ConfigError("APP_ENC_KEY_ID должен быть длиной 1..64")

    settings = Settings(
        app_db_url=Secret(get("APP_DB_URL")),
        enc_key=Secret(get("APP_ENC_KEY")),
        enc_key_id=key_id,
        admin_logins=parse_admin_logins(get("ADMIN_LOGINS")),
        ldap_url=get("LDAP_URL"),
        ldap_base_dn=get("LDAP_BASE_DN"),
        ldap_user_filter=get("LDAP_USER_FILTER"),
        ldap_bind_dn=get("LDAP_BIND_DN"),
        ldap_bind_password=Secret(get("LDAP_BIND_PASSWORD")),
        ldap_use_tls=_truthy(get("LDAP_USE_TLS", "false")),
        jira_base_url=get("JIRA_BASE_URL"),
        llm_base_url=get("LLM_BASE_URL"),
    )
    settings.enc_key_bytes()  # проверка формата ключа при запуске
    return settings
