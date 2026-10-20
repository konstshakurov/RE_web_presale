# -*- coding: utf-8 -*-
"""Адаптер Jira для приложения (Dev-18, KON-34).

Тонкий слой, реализующий :class:`app.integrations.jira.port.JiraPort` поверх
``src/jira_client``. Приложение вызывает **только** адаптер: HTTP, cookie,
коды ответов и классы исключений остаются внутри.

Что здесь важно:

* вход — один запрос ``POST /rest/auth/1/session``; пароль не сохраняется и не
  логируется, дальше приложение работает только с запечатанным токеном;
* токен в БД и в коде приложения существует исключительно в виде
  ``TokenVault`` (AES-256-GCM, ключ из ``JIRA_TOKEN_ENCRYPTION_KEY``);
* все отказы проходят ``errors.classify`` и получают единый признак ``code``;
* повторы выключены, лимит 10 с на вызов (NFR-009) задаёт ``JiraConfig``.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

import httpx
from jira_client import (
    AUTH_REASON_FAILED,
    AUTH_REASON_NO_CREDENTIALS,
    AUTH_REASON_NO_PERMISSION,
    CryptoError,
    JiraClient,
    JiraConfig,
    JiraError,
    SessionLoginProvider,
    TokenVault,
    load_dotenv,
)
from jira_client import fields as F

from .errors import (
    CODE_JIRA_ERROR,
    CODE_JIRA_UNAVAILABLE,
    CODE_NEEDS_REAUTHORIZATION,
    JiraIntegrationError,
    classify,
)
from .port import (
    RATE_CURRENCY_IDS,
    RATE_PROPOSER_PRODUCTION,
    RATE_UNIT_IDS,
    RATE_VAT_IDS,
    SESSION_JIRA_UNAVAILABLE,
    SESSION_NEEDS_REAUTHORIZATION,
    SESSION_NO_PERMISSION,
    SESSION_OK,
    AllowedValue,
    SealedSession,
    SessionCheck,
    TransitionField,
    TransitionView,
)


class JiraAdapter:
    """Реализация :class:`JiraPort` поверх ``jira_client``."""

    #: Каноническое поле связи «эпик ↔ потребность» (FR-028). Альтернатива
    #: ``customfield_11032`` остаётся открытым вопросом владельцу процесса.
    epic_link_field: str = F.EPIC_LINK

    def __init__(
        self,
        *,
        config: Optional[JiraConfig] = None,
        vault: Optional[TokenVault] = None,
        http_client: Optional[httpx.Client] = None,
        read_fields: Optional[Sequence[str]] = None,
        epic_fields: Optional[Sequence[str]] = None,
        env_file: Optional[str] = None,
    ) -> None:
        if env_file:
            # ``.env`` уже в .gitignore; сюда попадают только базовый URL,
            # cookie-имя и ключ шифрования — никаких паролей приложение не берёт.
            load_dotenv(env_file)
        self.config = config if config is not None else JiraConfig.from_env()
        self._vault = vault if vault is not None else TokenVault.from_env()
        #: Список полей настраивается (критерий приёмки Dev-18).
        self.read_fields: Sequence[str] = (
            tuple(read_fields) if read_fields else tuple(F.DEMAND_READ_FIELDS)
        )
        self.epic_fields: Sequence[str] = (
            tuple(epic_fields) if epic_fields else tuple(F.EPIC_READ_FIELDS)
        )
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(
            timeout=self.config.timeout,
            verify=self.config.verify_tls,
            follow_redirects=False,
        )

    # -- жизненный цикл -----------------------------------------------------

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "JiraAdapter":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def describe(self) -> Dict[str, Any]:
        """Безопасное описание для логов: без токенов, cookie и логинов."""
        return {
            "adapter": "jira",
            "epic_link_field": self.epic_link_field,
            "read_fields": len(self.read_fields),
            "epic_fields": len(self.epic_fields),
            "config": self.config.safe_summary(),
        }

    # -- внутреннее ---------------------------------------------------------

    def _fields(self, fields: Optional[Sequence[str]], default: Sequence[str]) -> List[str]:
        """Настроенный список полей: переданный вызовом, иначе свой адаптера."""
        return list(fields) if fields is not None else list(default)

    def _open_token(self, sealed_token: str) -> str:
        """Распечатать токен. Наружу открытый токен не возвращается никому."""
        if not sealed_token:
            raise JiraIntegrationError(
                "Сессия не создана: требуется вход",
                code=CODE_NEEDS_REAUTHORIZATION,
                reason=AUTH_REASON_NO_CREDENTIALS,
            )
        try:
            return self._vault.open(sealed_token)
        except CryptoError as exc:
            # Токен повреждён или другой ключ: сессия утрачена, нужен вход.
            raise classify(exc) from exc

    @contextmanager
    def _client(self, sealed_token: str) -> Iterator[JiraClient]:
        """Клиент на одну операцию, с открытым токеном.

        HTTP-клиент переиспользуется (пул соединений), ``JiraClient.close()``
        его не закрывает.

        Отказы тело операции переводятся на единую карту ошибок: наружу из
        порта не выходит ни один класс ``jira_client`` и ни один код HTTP.
        """
        token = self._open_token(sealed_token)
        config = replace(self.config, session_token=token)
        client = JiraClient(config=config, http_client=self._http)
        try:
            yield client
        except JiraError as exc:
            raise classify(exc) from exc
        finally:
            client.close()

    # -- вход ---------------------------------------------------------------

    def login(self, username: str, password: str) -> SealedSession:
        """Обменять логин и пароль на запечатанную сессию Jira.

        Пароль живёт только в теле одного запроса: он не сохраняется, не
        логируется и не попадает в результат. Приложение получает
        ``sealed_token`` — ``base64(nonce || ciphertext || tag)`` от
        ``TokenVault``, плюс ``created_at`` (Dev-18 показывает возраст
        сессии и решает, не пора ли просить пароль повторно).

        Отказы: ``auth_failed`` (неверный пароль), ``login_blocked`` (Jira
        просит подтвердить вход в браузере), ``jira_unavailable`` (сеть,
        TLS, таймаут, 5xx) — это разные признаки, и путать их нельзя.
        """
        provider = SessionLoginProvider(
            base_url=self.config.base_url,
            cookie_name=self.config.session_cookie,
            timeout=self.config.timeout,
            verify_tls=self.config.verify_tls,
            http_client=self._http,
        )
        try:
            session = provider.login(username, password)
        except JiraError as exc:
            raise classify(exc) from exc

        try:
            sealed = self._vault.seal(session.token)
        except CryptoError as exc:
            raise classify(exc) from exc

        return SealedSession(
            sealed_token=sealed,
            cookie_name=session.cookie_name,
            created_at=session.created_at,
            username=username,
        )

    def check_session(self, sealed_token: str) -> SessionCheck:
        """Проверить сессию ``GET /rest/api/2/myself``. Не бросает исключений.

        Три состояния контракта Dev-18 — ``ok``, ``needs_reauthorization``,
        ``jira_unavailable`` — плюс ``no_permission``, которое ADR-005
        требует различать с ними («нет прав» не лечится повторным входом).
        """
        if not sealed_token:
            return SessionCheck(
                state=SESSION_NEEDS_REAUTHORIZATION,
                reason=AUTH_REASON_NO_CREDENTIALS,
                summary="Сессия не создана",
            )
        try:
            with self._client(sealed_token) as client:
                result = client.check_session()
        except JiraIntegrationError as exc:
            state = (
                SESSION_NEEDS_REAUTHORIZATION
                if exc.needs_reauthorization
                else SESSION_JIRA_UNAVAILABLE
            )
            return SessionCheck(
                state=state, reason=exc.reason or exc.code, status=exc.status, summary=exc.message
            )
        return _check_from(result)

    # -- чтение -------------------------------------------------------------

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
        """Только задачи, изменённые после ``since`` (``updated``, FR-028).

        ``since`` — ``YYYY-MM-DD HH:mm`` в формате JQL. Сортировка
        ``updated ASC`` обязательна: без неё водяной знак не двигается и
        часть новых задач теряется.
        """
        extra: Dict[str, Any] = {}
        if project:
            extra["project"] = project
        if issuetype:
            extra["issuetype"] = issuetype
        with self._client(sealed_token) as client:
            return list(
                client.search_updated_since(
                    since,
                    fields=self._fields(fields, self.read_fields),
                    max_pages=max_pages,
                    **extra,
                )
            )

    def get_issue(
        self,
        sealed_token: str,
        issue_key: str,
        *,
        fields: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """``GET /rest/api/2/issue/{key}`` — список полей настраивается."""
        with self._client(sealed_token) as client:
            return client.get_issue(issue_key, self._fields(fields, self.read_fields))

    def get_epic(
        self,
        sealed_token: str,
        epic_key: str,
        *,
        fields: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """``GET /rest/agile/1.0/epic/{key}`` — сам эпик.

        Ответ **плоский** (``name``, ``summary``, ``done`` …), а не
        ``{key, fields}``; параметр ``fields`` Agile API игнорирует.
        """
        with self._client(sealed_token) as client:
            return client.get_epic(epic_key, fields=self._fields(fields, self.epic_fields))

    def get_epic_children(
        self,
        sealed_token: str,
        epic_key: str,
        *,
        fields: Optional[Sequence[str]] = None,
        max_pages: int = 50,
    ) -> List[Dict[str, Any]]:
        """Дети эпика через Agile API, а не через JQL с ``parent``.

        Проба KON-30: ``parent = <эпик>`` в Jira 8.13.0 всегда даёт 0
        результатов, тогда как Agile API и ``"Epic Link" = <эпик>`` дают
        одинаковый ``total`` (282 на живой Jira).
        """
        with self._client(sealed_token) as client:
            return list(
                client.iter_epic_children(
                    epic_key,
                    fields=self._fields(fields, self.read_fields),
                    max_pages=max_pages,
                )
            )

    # -- запись (KON-42) ----------------------------------------------------

    @contextmanager
    def _write(self, sealed_token: str) -> Iterator[JiraClient]:
        """Клиент на одну операцию записи; ``ValueError`` клиента — ошибка запроса.

        ``JiraClient`` поднимает ``ValueError`` на заведомо негодных данных
        (пустой комментарий или файл, пустой набор полей). Это не сбой Jira, а
        неверный запрос приложения: переводим в ``jira_error`` и показываем
        текст как есть (FR-029) — так же делал мост ``assign.jira_write``.
        """
        try:
            with self._client(sealed_token) as client:
                yield client
        except ValueError as exc:
            raise JiraIntegrationError(str(exc), code=CODE_JIRA_ERROR) from exc

    def get_transitions(self, sealed_token: str, issue_key: str) -> List[TransitionView]:
        """Переходы из текущего статуса вместе с полями (``expand=transitions.fields``)."""
        with self._client(sealed_token) as client:
            return [_transition_view(t) for t in client.get_transitions(issue_key)]

    def do_transition(
        self,
        sealed_token: str,
        issue_key: str,
        transition_id: str,
        *,
        fields: Mapping[str, Any],
        comment: Optional[str] = None,
    ) -> None:
        """Переход; комментарий — через ``update.comment.add``.

        ``validate=False``: доступность перехода и обязательные поля сервис
        проверяет по свежему списку ``get_transitions``; повторный запрос
        переходов здесь — лишний вызов и лишние 10 с (NFR-009).
        """
        try:
            with self._write(sealed_token) as client:
                client.transition(
                    issue_key,
                    transition_id,
                    fields=fields or None,
                    comment=comment,
                    validate=False,
                )
        except JiraIntegrationError as exc:
            # D3 (проба KON-42): неизвестный/недоступный id перехода Jira 8.13 отвечает 500
            # «Внутренняя ошибка сервера». Повторять такой запрос бессмысленно: не
            # ``jira_unavailable``/retryable, а отказ с текстом Jira. 502/503/504 и таймаут
            # (status не 500) остаются «Jira недоступна».
            if exc.code == CODE_JIRA_UNAVAILABLE and exc.status == 500:
                raise JiraIntegrationError(
                    "Jira отклонила переход (внутренняя ошибка 500): переход %s неизвестен или "
                    "недоступен из текущего статуса" % transition_id,
                    code=CODE_JIRA_ERROR,
                    reason=exc.reason,
                    status=exc.status,
                    retryable=False,
                ) from exc
            raise

    def set_fields(self, sealed_token: str, issue_key: str, fields: Mapping[str, Any]) -> None:
        """``PUT /rest/api/2/issue/{key}`` — изменить поля задачи."""
        with self._write(sealed_token) as client:
            client.set_fields(issue_key, fields)

    def add_comment(self, sealed_token: str, issue_key: str, body: str) -> None:
        """Добавить комментарий. Пустое тело отклонит клиент (``ValueError``)."""
        with self._write(sealed_token) as client:
            client.add_comment(issue_key, body)

    def attach_file(
        self,
        sealed_token: str,
        issue_key: str,
        filename: str,
        content: bytes,
        mime_type: str,
    ) -> None:
        """Загрузить вложение.

        ``size_limit=0``: своих ограничений приложение не вводит, размер судит
        Jira (FR-026). Заголовок ``X-Atlassian-Token: no-check`` ставит клиент.
        """
        with self._write(sealed_token) as client:
            client.add_attachment(
                issue_key,
                filename=filename,
                content=content,
                mime_type=mime_type,
                size_limit=0,
            )

    def assign_candidate(self, sealed_token: str, issue_key: str, username: str) -> None:
        """Назначить исполнителя (``assignee``) по логину."""
        with self._write(sealed_token) as client:
            client.assign(issue_key, username)

    def set_rate(
        self,
        sealed_token: str,
        issue_key: str,
        *,
        amount: Any,
        vat: str,
        currency: str,
        unit: str,
        proposer: str = RATE_PROPOSER_PRODUCTION,
        username: Optional[str] = None,
    ) -> None:
        """Записать ставку кнопкой «Ставка» Jira (ScriptRunner, проба KON-99, CR-008).

        Защита от «200 без записи»: Jira принимает неверный id справочника и запрос без
        ``customfield_21105`` с кодом 200, но ничего не пишет. Поэтому: id проверяются до
        отправки, пустая ставка не уходит (Jira даёт 500), ``21105`` передаётся всегда, а после
        записи «Планируемая ставка» перечитывается и сверяется. Пароль и токен в сообщения не
        попадают.
        """
        value = _rate_amount(amount)
        for name, got, allowed in (
            ("НДС", vat, RATE_VAT_IDS),
            ("валюта", currency, RATE_CURRENCY_IDS),
            ("единица измерения", unit, RATE_UNIT_IDS),
            ("кто предлагает ставку", proposer, frozenset({RATE_PROPOSER_PRODUCTION})),
        ):
            if str(got) not in allowed:
                raise JiraIntegrationError(
                    "Ставка не отправлена: неизвестное значение справочника «%s»" % name,
                    code=CODE_JIRA_ERROR,
                )
        try:
            with self._write(sealed_token) as client:
                login = username or str((client.myself() or {}).get("name") or "")
                if not login:
                    raise JiraIntegrationError(
                        "Ставка не отправлена: логин пользователя Jira не определён",
                        code=CODE_JIRA_ERROR,
                    )
                client.request(
                    "POST",
                    RATE_ENDPOINT,
                    json_body={
                        "issueKey": issue_key,
                        "currentUserUsername": login,
                        F_RATE_PROPOSER: str(proposer),
                        F_RATE_AMOUNT: value,
                        F_RATE_VAT: str(vat),
                        F_RATE_CURRENCY: str(currency),
                        F_RATE_UNIT: str(unit),
                    },
                    headers={"Content-Type": "application/json;charset=utf-8"},
                )
                issue = client.get_issue(issue_key, [F_RATE_AMOUNT, F_RATE_VAT, F_RATE_CURRENCY,
                                                     F_RATE_UNIT])
        except JiraIntegrationError as exc:
            # Проба KON-99: 500 - пустая ставка или несуществующий ключ. Повтор того же запроса
            # бессмысленен: ошибка запроса, а не «Jira недоступна» (502/503/504 и таймаут
            # остаются недоступностью).
            if exc.code == CODE_JIRA_UNAVAILABLE and exc.status == 500:
                raise JiraIntegrationError(
                    "Jira отклонила запись ставки (внутренняя ошибка 500): проверьте ключ "
                    "потребности и значение ставки",
                    code=CODE_JIRA_ERROR,
                    reason=exc.reason,
                    status=exc.status,
                    retryable=False,
                ) from exc
            raise
        if not _rate_matches(
            (issue or {}).get("fields") or {}, value, vat=vat, currency=currency, unit=unit
        ):
            raise JiraIntegrationError(
                "Jira ответила 200, но ставка не записана: значение в потребности не изменилось",
                code=CODE_JIRA_ERROR,
            )


def _transition_view(transition: Any) -> TransitionView:
    """Перевести ``jira_client.Transition`` в :class:`TransitionView` порта.

    Единственное место, где форма ``jira_client`` пересекает границу порта:
    наружу уходят только типы порта.
    """
    target = getattr(transition, "to_status", None)
    to_status = (getattr(target, "name", "") or "") if target is not None else ""
    fields = tuple(
        TransitionField(
            field_id=spec.field_id,
            name=spec.name,
            required=bool(spec.required),
            allowed_values=tuple(
                AllowedValue(id=value.id or "", name=value.name)
                for value in (spec.allowed_values or [])
            ),
        )
        for spec in (transition.fields or [])
    )
    return TransitionView(
        id=str(transition.id),
        name=transition.name,
        to_status=to_status,
        fields=fields,
    )


#: Кнопка «Ставка» Jira (ScriptRunner, проба KON-99) и поля тела запроса.
RATE_ENDPOINT = "/rest/scriptrunner/latest/custom/rateCalculationLogic"
F_RATE_PROPOSER = "customfield_21105"
F_RATE_AMOUNT = "customfield_20809"
F_RATE_VAT = "customfield_20810"
F_RATE_CURRENCY = "customfield_20811"
F_RATE_UNIT = "customfield_20812"


def _rate_amount(amount: Any) -> str:
    """Ставка числом-строкой для Jira; пустая или нечисловая - отказ до отправки."""
    if amount is None or isinstance(amount, bool) or str(amount).strip() == "":
        raise JiraIntegrationError(
            "Ставка не отправлена: ставка не задана (Jira ответила бы 500)", code=CODE_JIRA_ERROR
        )
    try:
        dec = Decimal(str(amount).strip().replace(",", "."))
    except InvalidOperation:
        dec = None
    if dec is None or not dec.is_finite() or dec < 0:
        raise JiraIntegrationError(
            "Ставка не отправлена: ставка должна быть неотрицательным числом",
            code=CODE_JIRA_ERROR,
        )
    return format(dec.normalize(), "f")


def _option_id(raw: Any) -> Optional[str]:
    return str(raw["id"]) if isinstance(raw, dict) and raw.get("id") is not None else None


def _rate_matches(
    fields: Mapping[str, Any], amount: str, *, vat: str, currency: str, unit: str
) -> bool:
    """Перечитанные поля совпали с записанными. Сумма обязательна; id - если Jira их отдала."""
    try:
        if Decimal(str(fields.get(F_RATE_AMOUNT))) != Decimal(amount):
            return False
    except (InvalidOperation, ValueError, TypeError):
        return False
    for field_id, expected in ((F_RATE_VAT, vat), (F_RATE_CURRENCY, currency), (F_RATE_UNIT, unit)):
        got = _option_id(fields.get(field_id))
        if got is not None and got != str(expected):
            return False
    return True


def _check_from(result: Any) -> SessionCheck:
    """Перевести безопасный словарь ``JiraClient.check_session`` в состояние."""
    if not isinstance(result, Mapping):
        return SessionCheck(
            state=SESSION_JIRA_UNAVAILABLE,
            reason="error",
            summary="Неожиданный ответ клиента Jira",
        )
    reason = str(result.get("reason") or "")
    status = result.get("status") if isinstance(result.get("status"), int) else None
    summary = str(result.get("summary") or "")
    if result.get("ok"):
        return SessionCheck(state=SESSION_OK, reason=reason or "ok", status=status, summary=summary)
    if reason == AUTH_REASON_NO_PERMISSION:
        return SessionCheck(
            state=SESSION_NO_PERMISSION, reason=reason, status=status, summary=summary
        )
    if result.get("needs_reauthorization") or reason == AUTH_REASON_FAILED:
        # ``auth_failed`` на /myself означает, что cookie не прошла проверку:
        # сессии нет, нужен повторный ввод пароля.
        return SessionCheck(
            state=SESSION_NEEDS_REAUTHORIZATION, reason=reason, status=status, summary=summary
        )
    # Сбой Jira (5xx, сеть, неизвестное): ждём и повторяем, пароль не спрашиваем.
    return SessionCheck(
        state=SESSION_JIRA_UNAVAILABLE, reason=reason, status=status, summary=summary
    )


__all__ = ["JiraAdapter"]
