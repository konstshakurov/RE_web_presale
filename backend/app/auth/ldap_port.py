"""Порт LDAP: проверка пары логин/пароль. Детали сервера наружу не раскрываются."""
from __future__ import annotations

from typing import Protocol


class LdapUnavailable(RuntimeError):
    """Сервер LDAP недоступен, таймаут или сбой протокола. Текст без деталей сервера."""


class LdapPort(Protocol):
    def authenticate(self, login: str, password: str) -> bool:
        """True, если пользователь найден фильтром и bind с его паролем успешен.

        Неверный пароль, неизвестный логин и пустой пароль дают False (не различаются).
        Недоступность сервера -> ``LdapUnavailable``. Пароль нигде не сохраняется.
        """
        ...


def normalize_login(raw: str) -> str:
    """Логин в нижний регистр без пробелов по краям (app_user.ldap_login — lower)."""
    return raw.strip().lower()
