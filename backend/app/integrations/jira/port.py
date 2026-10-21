# -*- coding: utf-8 -*-
"""Порт приложения к Jira (Dev-18, KON-34; запись — KON-42).

Приложение знает **только** этот модуль: :class:`JiraPort` — интерфейс входа,
чтения и записи, :class:`SealedSession`, :class:`SessionCheck` и типы переходов
(:class:`TransitionView`, :class:`TransitionField`, :class:`AllowedValue`) —
результаты, ``errors`` — единая карта отказов. Реализация — ``adapter.JiraAdapter``
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

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

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


# Типы записи (KON-42). Той же формы, что и в узком порту ``app.assign.port``:
# сигнатуры методов записи совпадают, поэтому мост ``app.assign.jira_write``
# заменяется тонкой обёрткой без правки сервиса. Держим их здесь, а не
# импортируем из ``assign``: ``integrations`` не зависит от прикладных модулей,
# приложение знает только порт.


@dataclass(frozen=True)
class AllowedValue:
    """Значение справочника поля перехода: ``id`` и отображаемое имя."""

    id: str
    name: str


@dataclass(frozen=True)
class TransitionField:
    """Поле перехода из ``transitions[].fields`` (``expand=transitions.fields``)."""

    field_id: str
    name: str
    required: bool
    allowed_values: tuple[AllowedValue, ...] = ()


@dataclass(frozen=True)
class TransitionView:
    """Переход из текущего статуса: ``id``, имя, целевой статус и его поля."""

    id: str
    name: str
    to_status: str
    fields: tuple[TransitionField, ...] = field(default_factory=tuple)


# Справочники кнопки «Ставка» (проба KON-99, §3): id Jira строками.
RATE_PROPOSER_PRODUCTION = "22412"
RATE_PROPOSER_CLIENT = "22411"
RATE_VAT_YES = "20999"
RATE_VAT_NO = "21000"
RATE_CURRENCY_RUB = "21001"
RATE_CURRENCY_EUR = "21002"
RATE_CURRENCY_GBR = "21003"
RATE_CURRENCY_KZT = "21004"
RATE_CURRENCY_USD = "21005"
RATE_UNIT_HOUR = "21006"
RATE_VAT_IDS = frozenset({RATE_VAT_YES, RATE_VAT_NO})
RATE_CURRENCY_IDS = frozenset({RATE_CURRENCY_RUB, RATE_CURRENCY_EUR, RATE_CURRENCY_GBR,
                               RATE_CURRENCY_KZT, RATE_CURRENCY_USD})
RATE_UNIT_IDS = frozenset({RATE_UNIT_HOUR})
MAX_RATE_RUB = 1_000_000  # верхняя граница ставки, руб. (KON-103)


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

    # -- запись (KON-42) ----------------------------------------------------
    #
    # Сигнатуры совпадают с узким портом ``app.assign.port.JiraWritePort``:
    # мост ``app.assign.jira_write.JiraAdapterWritePort`` заменяется тонкой
    # обёрткой без правки сервиса. Отказы — ``errors.JiraIntegrationError``,
    # ни один класс ``jira_client`` и ни один код HTTP наружу не выходит.
    # Запись идёт от имени пользователя сессии: действие видно в истории Jira
    # под его учёткой (FR-025, BR-001).

    def get_transitions(self, sealed_token: str, issue_key: str) -> List[TransitionView]:
        """Переходы из текущего статуса вместе с полями (``expand=transitions.fields``).

        Права и доступность перехода приложение не проверяет — это делает Jira
        (FR-026): отданный ею список и есть разрешённые действия.
        """
        ...

    def do_transition(
        self,
        sealed_token: str,
        issue_key: str,
        transition_id: str,
        *,
        fields: Mapping[str, Any],
        comment: Optional[str] = None,
    ) -> None:
        """Выполнить переход.

        ``comment`` уходит через ``update.comment.add`` — единственная форма,
        которую принимает Jira 8.13.0; у перехода 171 «Кандидатов нет» он
        обязателен, хотя REST его обязательным не объявляет, поэтому передавать
        его всегда, когда он есть. Обязательные поля перехода проверяет сервис
        по свежему списку — здесь без повторного запроса переходов (NFR-009).
        """
        ...

    def set_fields(self, sealed_token: str, issue_key: str, fields: Mapping[str, Any]) -> None:
        """``PUT /rest/api/2/issue/{key}`` — изменить поля задачи (ставка, профиль)."""
        ...

    def add_comment(self, sealed_token: str, issue_key: str, body: str) -> None:
        """Добавить комментарий к задаче."""
        ...

    def attach_file(
        self,
        sealed_token: str,
        issue_key: str,
        filename: str,
        content: bytes,
        mime_type: str,
    ) -> None:
        """Загрузить вложение (заголовок ``X-Atlassian-Token: no-check``)."""
        ...

    def assign_candidate(self, sealed_token: str, issue_key: str, username: str) -> None:
        """Назначить исполнителя задачи (``assignee``) по логину.

        Обёртка над полем ``assignee``: формат значения у поля типа user
        подтверждает проба (Z3). Если логина нет в Jira, Jira отвечает отказом,
        и приложение показывает её текст как есть (FR-029).
        """
        ...

    def set_rate(
        self,
        sealed_token: str,
        issue_key: str,
        *,
        amount: Any,
        vat: str,
        currency: str,
        unit: str,
        proposer: str = "22412",
        username: Optional[str] = None,
    ) -> None:
        """Записать «Планируемую ставку» кнопкой «Ставка» Jira (CR-008, проба KON-99).

        ``POST /rest/scriptrunner/latest/custom/rateCalculationLogic`` (плагин
        ScriptRunner), а не поля перехода и не ``PUT /issue``. ``vat``, ``currency``,
        ``unit``, ``proposer`` - id справочников Jira строками (``RATE_VAT_*``,
        ``RATE_CURRENCY_*``, ``RATE_UNIT_HOUR``, ``RATE_PROPOSER_PRODUCTION``);
        ``username`` - логин пользователя сессии (``currentUserUsername``), без него
        берётся из ``/myself``.

        Jira отвечает 200 и без записи (неверный id, нет ``21105``), поэтому адаптер
        валидирует id до отправки, не шлёт пустую ставку и после записи перечитывает
        поле: расхождение - ``jira_error``. 500 (в т.ч. на несуществующий ключ) - ошибка
        запроса ``jira_error``, а не ``jira_unavailable``.
        """
        ...


__all__ = [
    "JiraPort",
    "RATE_PROPOSER_PRODUCTION",
    "RATE_PROPOSER_CLIENT",
    "RATE_VAT_YES",
    "RATE_VAT_NO",
    "RATE_CURRENCY_RUB",
    "RATE_CURRENCY_EUR",
    "RATE_CURRENCY_GBR",
    "RATE_CURRENCY_KZT",
    "RATE_CURRENCY_USD",
    "RATE_UNIT_HOUR",
    "RATE_VAT_IDS",
    "RATE_CURRENCY_IDS",
    "RATE_UNIT_IDS",
    "SealedSession",
    "SessionCheck",
    "AllowedValue",
    "TransitionField",
    "TransitionView",
    "SESSION_OK",
    "SESSION_NEEDS_REAUTHORIZATION",
    "SESSION_JIRA_UNAVAILABLE",
    "SESSION_NO_PERMISSION",
    "SESSION_STATES",
]
