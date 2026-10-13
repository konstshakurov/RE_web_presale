"""Адаптер LDAP на ldap3.

Два режима: прямой bind (задан LDAP_BIND_TEMPLATE, служебная учётка не нужна) или
search пользователя (необязательной служебной учётной записью) и bind с его паролем.
После успешного bind тем же соединением читается запись пользователя (cn, mail).
"""
from __future__ import annotations

import logging
import math
import ssl
from urllib.parse import urlparse

from ldap3 import BASE, SIMPLE, Connection, Server, Tls
from ldap3.core.exceptions import (
    LDAPBindError,
    LDAPException,
    LDAPInvalidCredentialsResult,
    LDAPSASLPrepError,
)
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn

from ..config import ConfigError, Settings
from .ldap_port import LdapProfile, LdapUnavailable

log = logging.getLogger(__name__)

LOGIN_PLACEHOLDER = "{login}"
_MAX_PASSWORD = 1024


def build_filter(template: str, login: str) -> str:
    """Подставить логин в фильтр с экранированием (защита от LDAP-инъекции)."""
    return template.replace(LOGIN_PLACEHOLDER, escape_filter_chars(login))


def build_bind_dn(template: str, login: str) -> str:
    """Подставить логин в шаблон DN с экранированием RDN (запятая, плюс, кавычки и т.п.)."""
    return template.replace(LOGIN_PLACEHOLDER, escape_rdn(login))


def _first(value) -> str:
    """Первое значение атрибута ldap3 (список или скаляр) как строка; пусто, если нет."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip() if value is not None else ""


class Ldap3Adapter:
    """Допущение: если задан ``bind_template`` (например ``uid={login},cn=users,...``), bind
    делается DN из шаблона без поиска; иначе search по LDAP_USER_FILTER (``(uid={login})``)."""

    def __init__(
        self,
        url: str,
        base_dn: str,
        user_filter: str,
        bind_dn: str = "",
        bind_password: str = "",
        use_tls: bool = False,
        timeout: float = 5.0,
        bind_template: str = "",
    ) -> None:
        if not url:
            raise ConfigError("LDAP: нужен LDAP_URL")
        if bind_template:
            if LOGIN_PLACEHOLDER not in bind_template:
                raise ConfigError("LDAP: LDAP_BIND_TEMPLATE должен содержать плейсхолдер {login}")
        elif not base_dn or LOGIN_PLACEHOLDER not in user_filter:
            raise ConfigError(
                "LDAP: нужны LDAP_BASE_DN и LDAP_USER_FILTER с плейсхолдером {login} "
                "(или LDAP_BIND_TEMPLATE)"
            )
        self._bind_template = bind_template
        self._base_dn = base_dn
        self._filter = user_filter
        self._bind_dn = bind_dn
        self._bind_password = bind_password
        # ldap3 на Linux упаковывает таймауты через struct: нужен int
        self._timeout = max(1, math.ceil(timeout))
        # ldap3 не принимает хвостовой "/" в URL (ldap://host:389/): разбираем сами
        parsed = urlparse(url.strip())
        scheme = parsed.scheme
        if not parsed.hostname:
            raise ConfigError("LDAP: LDAP_URL должен иметь вид ldap://host[:port]")
        self._starttls = use_tls and scheme == "ldap"
        tls = Tls(validate=ssl.CERT_REQUIRED) if (use_tls or scheme == "ldaps") else None
        self._server = Server(
            parsed.hostname, port=parsed.port or (636 if scheme == "ldaps" else 389),
            use_ssl=scheme == "ldaps", get_info="NONE", connect_timeout=self._timeout, tls=tls,
        )

    @classmethod
    def from_settings(cls, s: Settings) -> Ldap3Adapter:
        return cls(
            s.ldap_url, s.ldap_base_dn, s.ldap_user_filter, s.ldap_bind_dn,
            s.ldap_bind_password.reveal(), s.ldap_use_tls, s.ldap_timeout_sec,
            s.ldap_bind_template,
        )

    def _connection(self, user: str | None, password: str | None) -> Connection:
        return Connection(
            self._server, user=user, password=password, authentication=SIMPLE,
            receive_timeout=self._timeout, raise_exceptions=True, auto_bind=False,
        )

    def _bind(self, conn: Connection) -> None:
        if self._starttls:
            conn.open()
            conn.start_tls()
        conn.bind()

    def _find_dn(self, login: str) -> str | None:
        conn = self._connection(self._bind_dn or None, self._bind_password or None)
        try:
            self._bind(conn)
            conn.search(
                self._base_dn, build_filter(self._filter, login),
                attributes=["1.1"], size_limit=2, time_limit=int(self._timeout) or 1,
            )
            entries = conn.entries
            # ровно одна запись; 0 или >1 - отказ (неоднозначный логин не пускаем)
            return str(entries[0].entry_dn) if len(entries) == 1 else None
        finally:
            if conn.bound:
                conn.unbind()

    @staticmethod
    def _read_profile(conn: Connection, dn: str) -> tuple[str, str]:
        """cn и mail записи самого пользователя; любой сбой - пустые значения, вход не ломаем."""
        try:
            conn.search(
                dn, "(objectClass=*)", search_scope=BASE, attributes=["cn", "mail"],
                size_limit=1, time_limit=1,
            )
            if len(conn.entries) != 1:
                return "", ""
            attrs = conn.entries[0].entry_attributes_as_dict
            return _first(attrs.get("cn")), _first(attrs.get("mail"))
        except Exception as exc:  # noqa: BLE001 - профиль необязателен
            log.warning("LDAP: профиль пользователя не прочитан: %s", type(exc).__name__)
            return "", ""

    def authenticate_profile(self, login: str, password: str) -> LdapProfile | None:
        """None - отказ; иначе профиль (имя = логин, если cn не прочитан)."""
        if not login or not password or "\x00" in login + password or len(password) > _MAX_PASSWORD:
            return None  # пустой пароль = unauthenticated bind, недопустим
        if self._bind_template:
            dn: str | None = build_bind_dn(self._bind_template, login)
        else:
            try:
                dn = self._find_dn(login)
            except LDAPInvalidCredentialsResult:
                # неверная сервисная учётка - ошибка конфига, а не пароля пользователя
                log.error("LDAP: отказ сервисной учётки (проверьте LDAP_BIND_DN)")
                raise LdapUnavailable("ldap service bind failed") from None
            except (LDAPException, OSError) as exc:
                log.error("LDAP недоступен или сбой протокола: %s", type(exc).__name__)
                raise LdapUnavailable("ldap unavailable") from None
        if dn is None:
            return None
        conn = self._connection(dn, password)
        try:
            self._bind(conn)
            name, mail = self._read_profile(conn, dn)
            return LdapProfile(full_name=name or login, mail=mail)
        except (LDAPBindError, LDAPInvalidCredentialsResult, LDAPSASLPrepError):
            # SASLprep запрещает управляющие символы в пароле: это неверный пароль (KON-67),
            # а не недоступность LDAP
            return None
        except (LDAPException, OSError) as exc:
            # деталей сервера и значений аргументов в лог не пишем: только класс ошибки
            log.error("LDAP недоступен или сбой протокола: %s", type(exc).__name__)
            raise LdapUnavailable("ldap unavailable") from None
        finally:
            if conn.bound:
                conn.unbind()

    def authenticate(self, login: str, password: str) -> bool:
        return self.authenticate_profile(login, password) is not None
