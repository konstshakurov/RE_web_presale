"""JSON-логи и фильтр, вырезающий секреты (NFR-006).

Вырезаются: значения паролей, токенов, ключей, заголовки Authorization / Cookie / Set-Cookie
(в тексте и в полях ``extra``), userinfo в URL и заранее известные значения секретов конфига.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime

REDACTED = "***"

_SENSITIVE_WORDS = (
    "password|passwd|pwd|token|secret|api[_-]?key|jsessionid|sessionid|session[_-]?id|enc[_-]?key"
)
_VALUE = r"""("[^"]*"|'[^']*'|[^\s,;&}'"]+)"""

_HEADER_RE = re.compile(
    r"""(?ix)(["']?(?:proxy-)?(?:authorization|cookie|set-cookie)["']?\s*[:=]\s*)"""
    r"""("[^"\r\n]*"|'[^'\r\n]*'|[^\r\n]*)"""
)
_KV_RE = re.compile(
    r"""(?ix)(["']?[\w-]*(?:""" + _SENSITIVE_WORDS + r""")[\w-]*["']?\s*[:=]\s*)""" + _VALUE
)
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_URL_USERINFO_RE = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)")
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(" + _SENSITIVE_WORDS + r"|authorization|cookie|set-cookie)"
)
_MIN_SECRET_LEN = 4


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Вырезать секреты из строки."""
    for value in sorted({s for s in secrets if s and len(s) >= _MIN_SECRET_LEN}, key=len,
                        reverse=True):
        text = text.replace(value, REDACTED)
    text = _HEADER_RE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _KV_RE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _BEARER_RE.sub(lambda m: m.group(1) + " " + REDACTED, text)
    text = _URL_USERINFO_RE.sub(lambda m: m.group(1) + REDACTED + m.group(2), text)
    return text


def _redact_obj(obj: object, secrets: Iterable[str]) -> object:
    if isinstance(obj, str):
        return redact(obj, secrets)
    if isinstance(obj, dict):
        return {
            k: REDACTED if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k)
            else _redact_obj(v, secrets)
            for k, v in obj.items()
        }
    if isinstance(obj, list | tuple):
        return [_redact_obj(v, secrets) for v in obj]
    if isinstance(obj, int | float | bool) or obj is None:
        return obj
    return redact(str(obj), secrets)


class RedactingFilter(logging.Filter):
    """Фильтр логгера/хендлера: схлопывает сообщение и вырезает секреты, включая extra."""

    _STD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def add_secrets(self, values: Iterable[str]) -> None:
        self._secrets.extend(v for v in values if v)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self._secrets)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text, self._secrets)
        for key, value in list(record.__dict__.items()):
            if key in self._STD:
                continue
            if _SENSITIVE_KEY_RE.search(key):
                record.__dict__[key] = REDACTED
            else:
                record.__dict__[key] = _redact_obj(value, self._secrets)
        return True


class JsonFormatter(logging.Formatter):
    """Одна JSON-строка на запись."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in RedactingFilter._STD and not key.startswith("_"):
                payload[key] = value
        if record.exc_text:
            payload["exc"] = record.exc_text
        elif record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(secrets: Iterable[str] = (), level: int = logging.INFO,
                      stream=None) -> RedactingFilter:
    """Корневой логгер: JSON в stdout, фильтр на хендлере (покрывает все логгеры)."""
    flt = RedactingFilter(secrets)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(flt)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    return flt
