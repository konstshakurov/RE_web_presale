"""Синхронизация Jira (KON-35, FR-028/020/034/035, ADR-004): опрос, кэш, штамп, отказы.

Доступ к Jira - только через ``JiraPort``. Фоновый опрос идёт токеном активного пользователя
(только чтение); без активных пользователей опрос приостановлен. Пароль нигде не хранится:
сервис оперирует запечатанным токеном из ``secret_store`` (kind = jira_session).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ..logging_setup import redact
from ..secrets import SecretsError, SecretStore
from .mapping import (
    EMPLOYEE_FIELDS,
    EPIC_FIELDS,
    KEY_RE,
    NEED_FIELDS,
    NeedRow,
    jql_time,
    map_employee,
    map_epic_rules,
    map_need,
)
from .repo import SCOPE_EMPLOYEES, SCOPE_NEEDS, EpicRow, SyncRepo
from .settings import SyncSettings

log = logging.getLogger(__name__)

# Состояния штампа «актуально на» (поле ``state`` ответов API)
STATE_OK = "ok"
STATE_NEVER = "never_synced"  # опроса ещё не было, кэш пуст
STATE_PAUSED = "paused"  # нет активных пользователей: «опрос приостановлен»
STATE_NEEDS_REAUTH = "needs_reauthorization"  # 401: сессия Jira истекла
STATE_NO_PERMISSION = "no_permission"  # Jira отказала по правам
STATE_UNAVAILABLE = "jira_unavailable"  # сеть, таймаут, 5xx
STATE_JIRA_ERROR = "jira_error"  # прочий отказ Jira, текст как есть
STATE_NOT_ENABLED = "not_enabled"  # JIRA_BASE_URL не задан
STATE_ERROR = "error"  # внутренний сбой синхронизации

_FAILURE_CODES = {
    STATE_NEEDS_REAUTH, STATE_NO_PERMISSION, STATE_UNAVAILABLE, STATE_JIRA_ERROR,
    STATE_NOT_ENABLED, STATE_ERROR,
}
_MAX_MESSAGE = 2000
#: Давно, но валидно для JQL: полная выгрузка сотрудников
_EPOCH_JQL = datetime(2000, 1, 1, tzinfo=UTC)


class SyncError(Exception):
    """Отказ синхронизации; ``message`` - текст Jira как есть (без токенов)."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class SyncInfo:
    """Штамп «актуально на» и состояние опроса для UI."""

    scope: str
    state: str
    stale: bool  # данные могут отставать: опрос не ok или приостановлен
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    code: str = ""
    message: str = ""
    paused: bool = False
    interval_sec: float = 0.0


@dataclass
class SyncResult:
    scope: str
    ok: bool = False
    paused: bool = False
    fetched: int = 0
    upserted: int = 0
    skipped: int = 0
    rates_dropped: int = 0
    code: str = ""
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EpicView:
    epic_key: str
    name: str
    summary: str | None
    rules: list[dict[str, str]]
    fetched_at: datetime | None
    stale: bool
    error: SyncError | None
    needs_total: int


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _clean(message: str) -> str:
    return redact(message or "")[:_MAX_MESSAGE]


def _to_sync_error(exc: BaseException) -> SyncError:
    """Единая карта отказов: 401 / нет прав / недоступность различаются (FR-029)."""
    from ..integrations.jira import (
        CODE_JIRA_UNAVAILABLE,
        CODE_NEEDS_REAUTHORIZATION,
        CODE_NO_PERMISSION,
        classify,
    )

    err = classify(exc)
    code = {
        CODE_NEEDS_REAUTHORIZATION: STATE_NEEDS_REAUTH,
        CODE_NO_PERMISSION: STATE_NO_PERMISSION,
        CODE_JIRA_UNAVAILABLE: STATE_UNAVAILABLE,
    }.get(err.code, STATE_JIRA_ERROR)
    return SyncError(code, _clean(err.message), err.status)


class SyncService:
    def __init__(
        self,
        repo: SyncRepo,
        jira: Any,  # JiraPort или None, если Jira не включена
        store: SecretStore,
        settings: SyncSettings,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repo = repo
        self._jira = jira
        self._store = store
        self._settings = settings
        self._now = now
        # токены, которые Jira уже отвергла: login -> secret.updated_at (повторный вход меняет)
        self._dead: dict[str, datetime] = {}
        self._dead_lock = threading.Lock()

    @property
    def settings(self) -> SyncSettings:
        return self._settings

    @property
    def repo(self) -> SyncRepo:
        return self._repo

    # --- кто опрашивает ---

    def has_active_pollers(self) -> bool:
        return bool(self._repo.list_pollers())

    def _live_pollers(self) -> tuple[list[str], bool]:
        """(логины с неотвергнутым токеном, есть ли хоть кто-то активный)."""
        pollers = self._repo.list_pollers()
        with self._dead_lock:
            live = [p.login for p in pollers if self._dead.get(p.login) != p.secret_updated_at]
        return live, bool(pollers)

    def _mark_dead(self, login: str) -> None:
        for p in self._repo.list_pollers():
            if p.login == login:
                with self._dead_lock:
                    self._dead[login] = p.secret_updated_at

    def _token(self, login: str) -> str | None:
        try:
            return self._store.load_jira_session(login)
        except SecretsError:
            log.warning("токен Jira не расшифрован (ключ/подмена)")
            return None

    # --- штамп ---

    def sync_info(self, scope: str) -> SyncInfo:
        """Состояние для UI: пометка при отказе Jira, «опрос приостановлен», штамп."""
        interval = (
            self._settings.needs_interval_sec if scope == SCOPE_NEEDS
            else self._settings.employees_interval_sec
        )
        if scope == SCOPE_EMPLOYEES and not self._repo.has_cv:
            return SyncInfo(scope=scope, state=STATE_NOT_ENABLED, stale=True, last_success_at=None,
                            last_attempt_at=None, code=STATE_NOT_ENABLED,
                            message="База сотрудников не настроена (CV_DB_URL)",
                            interval_sec=interval)
        state = self._repo.get_state(scope)
        paused = not self._repo.list_pollers()
        last_ok = state.last_success_at if state else None
        attempt = state.last_attempt_at if state else None
        code = (state.last_error_code or "") if state else ""
        message = (state.last_error or "") if state else ""
        if paused:
            current = STATE_PAUSED
        elif code:
            current = code if code in _FAILURE_CODES else STATE_ERROR
        elif last_ok is None:
            current = STATE_NEVER
        else:
            current = STATE_OK
        return SyncInfo(
            scope=scope, state=current, stale=current != STATE_OK, last_success_at=last_ok,
            last_attempt_at=attempt, code=code, message=message, paused=paused,
            interval_sec=interval,
        )

    # --- потребности ---

    def _needs_since(self) -> datetime:
        st = self._repo.get_state(SCOPE_NEEDS)
        overlap = timedelta(seconds=self._settings.overlap_sec)
        now = self._now()
        if st is None or not st.initial_load_done or st.last_sync_at is None:
            return now - timedelta(days=self._settings.initial_load_days)
        # курсор не уходит вперёд ни относительно окна опроса, ни относительно самой свежей
        # строки кэша: расхождение часов сервера и Jira не теряет изменения
        newest = self._repo.max_need_updated()
        base = min(st.last_sync_at, newest) if newest else st.last_sync_at
        return base - overlap

    def _fetch_needs(self, token: str) -> SyncResult:
        s = self._settings
        started = self._now()
        since = jql_time(self._needs_since(), s.jira_utc_offset_hours)
        issues = self._jira.search_incremental(
            token, since, project=s.need_project, issuetype=f'"{s.need_issuetype}"',
            fields=NEED_FIELDS,
        )
        rows = [r for r in (map_need(i) for i in issues if isinstance(i, dict)) if r]
        res = SyncResult(scope=SCOPE_NEEDS, ok=True, fetched=len(issues))
        res.skipped = len(issues) - len(rows)
        res.rates_dropped = sum(1 for r in rows if r.rate_dropped)
        res.upserted = self._repo.upsert_needs(self._link_employees(rows))
        self._repo.record_attempt_success(SCOPE_NEEDS, started)
        return res

    def _fetch_employees(self, token: str) -> SyncResult:
        s = self._settings
        started = self._now()
        issues = self._jira.search_incremental(
            token, jql_time(_EPOCH_JQL, s.jira_utc_offset_hours), project=s.employee_project,
            issuetype=f'"{s.employee_issuetype}"', fields=EMPLOYEE_FIELDS,
        )
        rows = [r for r in (map_employee(i) for i in issues if isinstance(i, dict)) if r]
        res = SyncResult(scope=SCOPE_EMPLOYEES, ok=True, fetched=len(issues))
        res.skipped = len(issues) - len(rows)
        res.upserted = self._repo.upsert_employees(rows)
        self._reconcile_employees()
        self._repo.record_attempt_success(SCOPE_EMPLOYEES, started)
        return res

    def _login_map(self) -> dict[str, str] | None:
        """Карта логин -> ключ карточки (только однозначные); None, если cv_db не доступна."""
        if not self._repo.has_cv:
            return None
        try:
            return self._repo.employee_login_map()
        except Exception:  # noqa: BLE001 - связь с сотрудником вторична, опрос из-за неё не падает
            log.warning("карта логинов сотрудников недоступна", exc_info=True)
            return None

    def _link_employees(self, rows: list[NeedRow]) -> list[NeedRow]:
        """employee_key = карточка с логином из «Профиля сотрудника», только если она одна;
        иначе None (не угадываем)."""
        mapping = self._login_map()
        if mapping is None:
            return rows
        return [
            replace(r, employee_key=mapping.get(r.employee_login)) if r.employee_login else r
            for r in rows
        ]

    def _reconcile_employees(self) -> None:
        """После опроса сотрудников: пересверить employee_key потребностей в кэше."""
        mapping = self._login_map()
        if mapping is None:
            return
        try:
            self._repo.reconcile_need_employees(mapping)
        except Exception:  # noqa: BLE001
            log.warning("сверка потребностей с сотрудниками не выполнена", exc_info=True)

    def _run_scope(self, scope: str, logins: list[str]) -> SyncResult:
        """Опрос под блокировкой. Перебирает токены: отвергнутый (401) пропускается, остальные
        отказы Jira (недоступна) прекращают перебор. Итог пишется в sync_state."""
        if scope == SCOPE_EMPLOYEES and not self._repo.has_cv:
            return SyncResult(scope=scope, code=STATE_NOT_ENABLED,
                              message="База сотрудников не настроена (CV_DB_URL)")
        if self._jira is None:
            err = SyncError(STATE_NOT_ENABLED, "Интеграция с Jira не включена (JIRA_BASE_URL)")
            self._repo.record_attempt_failure(scope, err.code, err.message)
            return SyncResult(scope=scope, code=err.code, message=err.message)
        fetch = self._fetch_needs if scope == SCOPE_NEEDS else self._fetch_employees
        with self._repo.try_lock(scope) as got:
            if not got:
                return SyncResult(scope=scope, code="busy", message="Синхронизация уже идёт")
            last: SyncError | None = None
            for login in logins:
                token = self._token(login)
                if token is None:
                    last = SyncError(STATE_NEEDS_REAUTH, "Сессия Jira не найдена: нужен вход")
                    continue
                try:
                    res = fetch(token)
                except Exception as exc:  # noqa: BLE001 - отказ Jira или сбой БД: в sync_state
                    err = self._classify(exc)
                    last = err
                    if err.code == STATE_NEEDS_REAUTH:
                        self._mark_dead(login)
                        continue
                    if err.code == STATE_NO_PERMISSION:
                        continue  # у другого пользователя права могут быть
                    break
                log.info(
                    "синхронизация выполнена",
                    extra={"scope": scope, "fetched": res.fetched, "upserted": res.upserted,
                           "skipped": res.skipped, "rates_dropped": res.rates_dropped},
                )
                return res
            err = last or SyncError(STATE_NEEDS_REAUTH, "Нет доступной сессии Jira")
            self._repo.record_attempt_failure(scope, err.code, err.message)
            log.warning("синхронизация не удалась", extra={"scope": scope, "code": err.code})
            return SyncResult(scope=scope, code=err.code, message=err.message)

    @staticmethod
    def _classify(exc: Exception) -> SyncError:
        if isinstance(exc, SyncError):
            return exc
        try:
            from ..integrations.jira import JiraIntegrationError
        except ImportError:  # jira_client вне образа: Jira не включена, отказов Jira нет
            JiraIntegrationError = ()  # noqa: N806
        if JiraIntegrationError and isinstance(exc, JiraIntegrationError):
            return _to_sync_error(exc)
        log.exception("сбой синхронизации")
        return SyncError(STATE_ERROR, f"Внутренняя ошибка синхронизации: {type(exc).__name__}")

    def poll(self, scope: str) -> SyncResult:
        """Фоновый цикл worker: токен активного пользователя; без активных - пауза."""
        live, any_active = self._live_pollers()
        if not any_active:
            return SyncResult(scope=scope, paused=True, code=STATE_PAUSED,
                              message="Опрос приостановлен: нет активных пользователей")
        if not live:
            # все активные токены отвергнуты: ждём повторного входа, Jira не дёргаем
            msg = "Сессия Jira истекла: нужна повторная авторизация"
            self._repo.record_attempt_failure(scope, STATE_NEEDS_REAUTH, msg)
            return SyncResult(scope=scope, code=STATE_NEEDS_REAUTH, message=msg)
        return self._run_scope(scope, live)

    def refresh(self, scope: str, login: str) -> SyncResult:
        """«Обновить» по кнопке: токен самого пользователя, инкрементально."""
        return self._run_scope(scope, [login])

    # --- эпики и правила клиента ---

    def get_epic(self, epic_key: str, login: str, *, force: bool = False) -> EpicView:
        """Эпик клиента: из кэша, если моложе TTL (1 ч), иначе из Jira. При отказе Jira отдаёт
        кэш с пометкой; кэша нет - SyncError с текстом Jira как есть."""
        if not KEY_RE.match(epic_key):
            raise SyncError("bad_request", "Некорректный ключ эпика")
        cached = self._repo.get_epic(epic_key)
        ttl = timedelta(seconds=self._settings.epic_ttl_sec)
        fresh = (
            cached is not None and cached.rules_fetched_at is not None
            and self._now() - cached.rules_fetched_at < ttl
        )
        if fresh and not force:
            return self._epic_view(cached, stale=False, error=None)
        try:
            self._load_epic(epic_key, login)
        except Exception as exc:  # noqa: BLE001
            err = self._classify(exc)
            if err.code == STATE_NEEDS_REAUTH:
                self._mark_dead(login)
            if cached is not None and cached.rules_fetched_at is not None:
                return self._epic_view(cached, stale=True, error=err)
            raise err from None
        return self._epic_view(self._repo.get_epic(epic_key), stale=False, error=None)

    def _load_epic(self, epic_key: str, login: str) -> None:
        if self._jira is None:
            raise SyncError(STATE_NOT_ENABLED, "Интеграция с Jira не включена (JIRA_BASE_URL)")
        token = self._token(login)
        if token is None:
            raise SyncError(STATE_NEEDS_REAUTH, "Сессия Jira не найдена: нужен вход")
        epic = self._jira.get_epic(token, epic_key)
        issue = self._jira.get_issue(token, epic_key, fields=EPIC_FIELDS)
        fields = issue.get("fields") if isinstance(issue, dict) else None
        fields = fields if isinstance(fields, dict) else {}
        summary = fields.get("summary") if isinstance(fields.get("summary"), str) else None
        name = (epic.get("name") if isinstance(epic, dict) else None) or summary or epic_key
        # дети эпика - только Agile API (parent в Jira 8.13 не работает)
        children = self._jira.get_epic_children(token, epic_key, fields=NEED_FIELDS)
        rows = [r for r in (map_need(i) for i in children if isinstance(i, dict)) if r]
        # связь с эпиком даёт сам Agile API, даже если Epic Link в ответе не пришёл
        rows = [r if r.epic_key == epic_key else replace(r, epic_key=epic_key) for r in rows]
        self._repo.save_epic(epic_key, str(name), summary, map_epic_rules(fields))
        self._repo.upsert_needs(self._link_employees(rows))

    def _epic_view(self, row: EpicRow, *, stale: bool, error: SyncError | None) -> EpicView:
        return EpicView(
            epic_key=row.epic_key, name=row.name, summary=row.summary,
            rules=row.rules or [], fetched_at=row.rules_fetched_at, stale=stale, error=error,
            needs_total=self._repo.count_epic_needs(row.epic_key),
        )
