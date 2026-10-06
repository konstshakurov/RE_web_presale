# -*- coding: utf-8 -*-
"""Порт приложения к Jira (Dev-18, KON-34).

Приложение знает **только** этот модуль: :class:`JiraPort` — интерфейс входа и
чтения, :class:`SealedSession` и :class:`SessionCheck` — результаты,
``errors`` — единая карта отказов. Реализация — ``adapter.JiraAdapter``
поверх ``src/jira_client``.

Зачем порт, а не прямые вызовы клиента:

* Dev-18 до KON-34 работает на фейке Jira (``architecture/06_plan_and_tasks.md``),
  поэтому тесты приложения не должны зависеть от сети и от ``httpx``;
* приложение не должно знать про ``httpx``, cookie и коды ответов — только про
  состояния, которые оно показывает пользователю.

Правило: сессионный токен наружу отдаётся **только зашифрованным**
(``TokenVault``, AES-256-GCM); пароль в порт не передаётся никогда, кроме
вызова ``login``, и не сохраняется.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable

# ---------------------------------------------------------------------------
# Состояния сессии (результат check_session)
# ---------------------------------------------------------------------------

#: Сессия жива, чтение возможно.
SESSION_OK = "ok"
#: Сессии нет или истекла: нужен повторный ввод пароля.
#: 401 ``login_required`` / ``invalid_cookie_or_expired`` / нет cookie.
SESSION_NEEDS_REAUTHORIZATION = "needs_reauthorization"
#: Jira недоступна: сеть, TLS, таймаут, 5xx. Пароль не вводить — повторить позже.
SESSION_JIRA_UNAVAILABLE = "jira_unavailable"
#: Аутентификация прошла, но прав не хватает. Повторный ввод пароля не поможет
#: (ADR-005): это единственный случай, когда отказ не лечится ни входом, ни
#: ожиданием.
SESSION_NO_PERMISSION = "no_permission"

#: Допустимые состояния ``check_session``. Первые три — контракт Dev-18;
#: четвёртое добавлено, потому что ADR-005 требует различать «нет прав» и
#: «сессия истекла», а единая карта ошибок называет ``no_permission`` отдельно.
SESSION_STATES = (
    SESSION_OK,
    SESSION_NEEDS_REAUTHORIZATION,
    SESSION_JIRA_UNAVAILABLE,
    SESSION_NO_PERMISSION,
)


@dataclass(frozen=True, repr=False)
class SealedSession:
    """Успешный вход: токен запечатан, пароль не сохранён.

    ``sealed_token`` — то, что приложение хранит в БД и передаёт обратно в
    порт. Это ``base64(nonce || ciphertext || tag)`` от ``TokenVault``; в
    открытом виде токен порт отдаёт только внутрь адаптера.
    """

    sealed_token: str
    cookie_name: str
    #: Когда сессия создана (UTC). Dev-18 показывает это пользователю и
    #: решает, не пора ли просить пароль повторно.
    created_at: datetime
    #: Логин, которому принадлежит сессия. Никогда не пишется в лог.
    username: str = ""

    @property
    def age_seconds(self) -> float:
        """Сколько секунд прошло с момента входа."""
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()

    def describe(self) -> Dict[str, Any]:
        """Безопасное описание для логов: ни токена, ни логина."""
        return {
            "cookie": self.cookie_name,
            "sealed_length": len(self.sealed_token),
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "username_set": bool(self.username),
        }

    def __repr__(self) -> str:
        return "<SealedSession cookie=%s created_at=%s sealed=***>" % (
            self.cookie_name,
            self.created_at.isoformat(timespec="seconds"),
        )


@dataclass(frozen=True)
class SessionCheck:
    """Результат ``check_session``.

    ``state`` отвечает на вопрос «что делать приложению», ``reason`` — на
    вопрос «что именно сказала Jira». Значения ``reason`` — те же, что в
    ``jira_client``: ``no_credentials``, ``login_required``, ``no_permission``,
    ``auth_failed``, ``invalid_cookie_or_expired``, ``unknown``.
    """

    state: str
    reason: str = ""
    status: Optional[int] = None
    summary: str = ""

    @property
    def ok(self) -> bool:
        return self.state == SESSION_OK

    @property
    def needs_reauthorization(self) -> bool:
        return self.state == SESSION_NEEDS_REAUTHORIZATION

    @property
    def jira_unavailable(self) -> bool:
        return self.state == SESSION_JIRA_UNAVAILABLE

    def describe(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "status": self.status,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Порт
# ---------------------------------------------------------------------------


@runtime_checkable
class JiraPort(Protocol):
    """Что приложение может делать с Jira. Больше — ничего.

    Все операции чтения принимают ``sealed_token`` — результат :meth:`login`.
    Поле эпика — ``customfield_10101`` (``Epic Link``) в паре с Agile API;
    JQL с ``parent`` не используется: проба KON-30 показала 0 результатов.
    """

    def login(self, username: str, password: str) -> SealedSession:
        """Обменять логин и пароль на запечатанную сессию Jira."""
        ...

    def check_session(self, sealed_token: str) -> SessionCheck:
        """Проверить сессию ``GET /rest/api/2/myself``. Не бросает исключений
        на отказах Jira: результат нужен как логическое значение."""
        ...

    def search_incremental(
        self,
        sealed_token: str,
        since: str,
        *,
        project: Optional[str] = None,
        issuetype: Optional[str] = None,
        fields: Optional[Sequence[str]] = None,
        max_pages: int = 200,
    ) -> List[Dict[str, Any]]:
        """Только изменённые с ``since`` задачи: JQL по ``updated``,
        сортировка ``updated ASC`` (FR-028, опрос раз в 5 минут)."""
        ...

    def get_issue(
        self,
        sealed_token: str,
        issue_key: str,
        *,
        fields: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """``GET /rest/api/2/issue/{key}`` с настраиваемым списком полей."""
        ...

    def get_epic(
        self,
        sealed_token: str,
        epic_key: str,
        *,
        fields: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """``GET /rest/agile/1.0/epic/{key}`` — сам эпик.

        Ответ Agile API — **плоский** объект ``{id, key, name, summary,
        done, color, self}``, а не ``{key, fields}`` как у ``get_issue``.
        Параметр ``fields`` этот эндпоинт игнорирует (проверено вживую на
        эпике ``SA-3593``: независимо от ``fields`` всегда возвращаются
        ``name`` и ``summary``).
        """
        ...

    def get_epic_children(
        self,
        sealed_token: str,
        epic_key: str,
        *,
        fields: Optional[Sequence[str]] = None,
        max_pages: int = 50,
    ) -> List[Dict[str, Any]]:
        """Дети эпика через Agile API ``/rest/agile/1.0/epic/{key}/issue``.

        JQL с ``parent`` не применяется: в Jira 8.13.0 он всегда даёт 0
        результатов, а ``"Epic Link" = <эпик>`` и Agile API дают одинаковый
        ``total`` (проба KON-30).
        """
        ...


__all__ = [
    "JiraPort",
    "SealedSession",
    "SessionCheck",
    "SESSION_OK",
    "SESSION_NEEDS_REAUTHORIZATION",
    "SESSION_JIRA_UNAVAILABLE",
    "SESSION_NO_PERMISSION",
    "SESSION_STATES",
]
