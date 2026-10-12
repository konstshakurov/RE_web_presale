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
    cv_db_url: Secret = Secret("")  # роль cv_rw; нужен синхронизации сотрудников (KON-35)
    cv_max_file_mb: int = 10  # лимит файла по умолчанию; app_setting.max_file_mb приоритетнее
    admin_logins: tuple[str, ...] = ()
    ldap_url: str = ""
    ldap_base_dn: str = ""
    ldap_user_filter: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_template: str = ""  # DN с {login}: прямой bind без поиска
    ldap_bind_password: Secret = Secret("")
    ldap_use_tls: bool = False
    ldap_timeout_sec: float = 5.0
    session_ttl_hours: float = 8.0  # допущение ADR-008
    session_cookie_secure: bool = True  # False только для http-стенда
    jira_base_url: str = ""
    llm_base_url: str = ""
    llm_api_key: Secret = Secret("")  # ключ внутренней Qwen; только env (NFR-006)
    llm_model: str = ""  # имя модели внутренней Qwen для chat/completions
    llm_task_ttl_days: int = 7  # допущение: срок хранения завершённых задач LLM

    def enc_key_bytes(self) -> bytes:
        return _decode_key(self.enc_key.reveal())

    def secret_values(self) -> list[str]:
        """Значения секретов для фильтра логов."""
        values = [self.app_db_url, self.cv_db_url, self.enc_key, self.ldap_bind_password,
                  self.llm_api_key]
        return [s.reveal() for s in values if s]


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _positive_float(get, name: str, default: str) -> float:
    try:
        value = float(get(name, default))
    except ValueError:
        raise ConfigError(f"{name} должен быть числом") from None
    if value <= 0:
        raise ConfigError(f"{name} должен быть больше нуля")
    return value


def _int_range(get, name: str, default: int, lo: int, hi: int) -> int:
    raw = get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} должен быть целым числом") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} должен быть в пределах {lo}..{hi}")
    return value


def _cv_max_mb(raw: str) -> int:
    if not raw:
        return 10
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError("CV_MAX_FILE_MB должен быть целым числом") from None
    if not 1 <= value <= 10:  # потолок 10 МБ зашит в cv_db (ADR-009)
        raise ConfigError("CV_MAX_FILE_MB должен быть в пределах 1..10")
    return value


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
        cv_db_url=Secret(get("CV_DB_URL")),
        cv_max_file_mb=_cv_max_mb(get("CV_MAX_FILE_MB")),
        admin_logins=parse_admin_logins(get("ADMIN_LOGINS")),
        ldap_url=get("LDAP_URL"),
        ldap_base_dn=get("LDAP_BASE_DN"),
        ldap_user_filter=get("LDAP_USER_FILTER"),
        ldap_bind_dn=get("LDAP_BIND_DN"),
        ldap_bind_template=get("LDAP_BIND_TEMPLATE"),
        ldap_bind_password=Secret(get("LDAP_BIND_PASSWORD")),
        ldap_use_tls=_truthy(get("LDAP_USE_TLS", "false")),
        ldap_timeout_sec=_positive_float(get, "LDAP_TIMEOUT_SEC", "5"),
        session_ttl_hours=_positive_float(get, "SESSION_TTL_HOURS", "8"),
        session_cookie_secure=_truthy(get("SESSION_COOKIE_SECURE", "true")),
        jira_base_url=get("JIRA_BASE_URL"),
        llm_base_url=get("LLM_BASE_URL"),
        llm_api_key=Secret(get("LLM_API_KEY")),
        llm_model=get("LLM_MODEL"),
        llm_task_ttl_days=_int_range(get, "LLM_TASK_TTL_DAYS", 7, 1, 365),
    )
    settings.enc_key_bytes()  # проверка формата ключа при запуске
    return settings
