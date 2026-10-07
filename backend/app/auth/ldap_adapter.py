"""Адаптер LDAP на ldap3: search пользователя (сервисной учёткой), затем bind с его паролем."""
from __future__ import annotations

import logging
import ssl
from urllib.parse import urlparse

from ldap3 import SIMPLE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException, LDAPInvalidCredentialsResult
from ldap3.utils.conv import escape_filter_chars

from ..config import ConfigError, Settings
from .ldap_port import LdapUnavailable

log = logging.getLogger(__name__)

LOGIN_PLACEHOLDER = "{login}"
_MAX_PASSWORD = 1024


def build_filter(template: str, login: str) -> str:
    """Подставить логин в фильтр с экранированием (защита от LDAP-инъекции)."""
    return template.replace(LOGIN_PLACEHOLDER, escape_filter_chars(login))


class Ldap3Adapter:
    """Допущение: стандартная схема search + bind; формат логина задаёт LDAP_USER_FILTER
    с плейсхолдером ``{login}``, например ``(uid={login})``."""

    def __init__(
        self,
        url: str,
        base_dn: str,
        user_filter: str,
        bind_dn: str = "",
        bind_password: str = "",
        use_tls: bool = False,
        timeout: float = 5.0,
    ) -> None:
        if not url or not base_dn or LOGIN_PLACEHOLDER not in user_filter:
            raise ConfigError(
                "LDAP: нужны LDAP_URL, LDAP_BASE_DN и LDAP_USER_FILTER с плейсхолдером {login}"
            )
        self._base_dn = base_dn
        self._filter = user_filter
        self._bind_dn = bind_dn
        self._bind_password = bind_password
        self._timeout = timeout
        scheme = urlparse(url).scheme
        self._starttls = use_tls and scheme == "ldap"
        tls = Tls(validate=ssl.CERT_REQUIRED) if (use_tls or scheme == "ldaps") else None
        self._server = Server(url, get_info="NONE", connect_timeout=timeout, tls=tls)

    @classmethod
    def from_settings(cls, s: Settings) -> Ldap3Adapter:
        return cls(
            s.ldap_url, s.ldap_base_dn, s.ldap_user_filter, s.ldap_bind_dn,
            s.ldap_bind_password.reveal(), s.ldap_use_tls, s.ldap_timeout_sec,
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

    def authenticate(self, login: str, password: str) -> bool:
        if not login or not password or "\x00" in login + password or len(password) > _MAX_PASSWORD:
            return False  # пустой пароль = unauthenticated bind, недопустим
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
            return False
        conn = self._connection(dn, password)
        try:
            self._bind(conn)
            return True
        except (LDAPBindError, LDAPInvalidCredentialsResult):
            return False
        except (LDAPException, OSError) as exc:
            # деталей сервера и значений аргументов в лог не пишем: только класс ошибки
            log.error("LDAP недоступен или сбой протокола: %s", type(exc).__name__)
            raise LdapUnavailable("ldap unavailable") from None
        finally:
            if conn.bound:
                conn.unbind()
