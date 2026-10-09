"""SQL кэша Jira: app.jira_need / jira_epic / sync_state (роль app_rw) и cv.employee / cv.sync_state
(роль cv_rw, отдельный пул). Параметры только через плейсхолдеры; имена таблиц - константы модуля.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .mapping import EmployeeRow, NeedRow

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

SCOPE_NEEDS = "needs"
SCOPE_EMPLOYEES = "employees"

#: Ключи advisory-lock: один опрос потребностей/сотрудников одновременно (worker и «Обновить»).
LOCK_KEYS = {SCOPE_NEEDS: 3501, SCOPE_EMPLOYEES: 3502}


@dataclass(frozen=True)
class SyncStateRow:
    scope: str
    last_sync_at: datetime | None
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_error: str | None
    last_error_code: str | None
    last_error_at: datetime | None
    initial_load_done: bool


@dataclass(frozen=True)
class Poller:
    """Пользователь, чей Jira-токен можно использовать для фонового опроса."""
    login: str
    secret_updated_at: datetime


@dataclass(frozen=True)
class EpicRow:
    epic_key: str
    name: str
    summary: str | None
    rules: list[dict[str, str]] | None
    rules_fetched_at: datetime | None


@dataclass(frozen=True)
class NeedFilter:
    group: str | None = None  # None/'all' - все; 'other' - вне групп; число - id группы
    status: str | None = None
    epic_key: str | None = None
    query: str | None = None


_STATE_COLS = (
    "scope, last_sync_at, last_success_at, last_attempt_at, last_error, last_error_code, "
    "last_error_at"
)

_NEED_COLS = (
    "jira_key, title, description, requirements_text, status, epic_key, assignee, author, "
    "contract_type, rate_amount, rate_currency, rate_includes_vat, rate_unit, rate_proposer, "
    "jira_created_at, jira_updated_at, employee_login, employee_key"
)
_NEED_UPSERT = (
    f"INSERT INTO app.jira_need ({_NEED_COLS}) VALUES "
    "(%(jira_key)s, %(title)s, %(description)s, %(requirements_text)s, %(status)s, %(epic_key)s, "
    "%(assignee)s, %(author)s, %(contract_type)s, %(rate_amount)s, %(rate_currency)s, "
    "%(rate_includes_vat)s, %(rate_unit)s, %(rate_proposer)s, %(jira_created_at)s, "
    "%(jira_updated_at)s, %(employee_login)s, %(employee_key)s) "
    "ON CONFLICT (jira_key) DO UPDATE SET title = EXCLUDED.title, "
    "description = EXCLUDED.description, requirements_text = EXCLUDED.requirements_text, "
    "status = EXCLUDED.status, epic_key = EXCLUDED.epic_key, assignee = EXCLUDED.assignee, "
    "author = EXCLUDED.author, contract_type = EXCLUDED.contract_type, "
    "rate_amount = EXCLUDED.rate_amount, rate_currency = EXCLUDED.rate_currency, "
    "rate_includes_vat = EXCLUDED.rate_includes_vat, rate_unit = EXCLUDED.rate_unit, "
    "rate_proposer = EXCLUDED.rate_proposer, jira_created_at = EXCLUDED.jira_created_at, "
    "jira_updated_at = EXCLUDED.jira_updated_at, employee_login = EXCLUDED.employee_login, "
    # ключ сотрудника не затирается, пока логин тот же, а карточку не нашли (cv_db недоступна)
    "employee_key = CASE WHEN EXCLUDED.employee_login IS NULL THEN NULL "
    "WHEN EXCLUDED.employee_login IS NOT DISTINCT FROM app.jira_need.employee_login "
    "THEN COALESCE(EXCLUDED.employee_key, app.jira_need.employee_key) "
    "ELSE EXCLUDED.employee_key END, synced_at = now() "
    # старый ответ не затирает более свежую строку (эпик открыт параллельно с опросом)
    "WHERE app.jira_need.jira_updated_at <= EXCLUDED.jira_updated_at"
)

_STATE_TABLES = {SCOPE_NEEDS: "app.sync_state", SCOPE_EMPLOYEES: "cv.sync_state"}


def _need_where(f: NeedFilter) -> tuple[str, list[Any]]:
    conds: list[str] = []
    params: list[Any] = []
    g = (f.group or "all").strip().lower()
    if g == "other":
        conds.append("n.status NOT IN (SELECT status_name FROM app.status_group_status)")
    elif g != "all":
        conds.append(
            "n.status IN (SELECT status_name FROM app.status_group_status WHERE group_id = %s)"
        )
        params.append(int(g))
    if f.status:
        conds.append("n.status = %s")
        params.append(f.status)
    if f.epic_key:
        conds.append("n.epic_key = %s")
        params.append(f.epic_key)
    if f.query:
        like = "%" + f.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conds.append("(n.jira_key ILIKE %s OR n.title ILIKE %s)")
        params += [like, like]
    return (" WHERE " + " AND ".join(conds)) if conds else "", params


class SyncRepo:
    def __init__(self, app_pool: ConnectionPool, cv_pool: ConnectionPool | None = None) -> None:
        self._app = app_pool
        self._cv = cv_pool

    @property
    def has_cv(self) -> bool:
        return self._cv is not None

    # --- блокировка: один опрос на область ---

    @contextmanager
    def try_lock(self, scope: str) -> Iterator[bool]:
        """Session-level advisory lock на время опроса; False, если область уже опрашивается."""
        key = LOCK_KEYS[scope]
        with self._app.connection() as conn:
            got = conn.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
            try:
                yield bool(got)
            finally:
                if got:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (key,))

    # --- кто может опрашивать ---

    def list_pollers(self) -> list[Poller]:
        """Активные пользователи с живой сессией приложения и сохранённым токеном Jira;
        первым - тот, кто был активен последним."""
        with self._app.connection() as conn:
            rows = conn.execute(
                "SELECT s.owner_login, s.updated_at FROM app.secret_store s "
                "JOIN app.app_user u ON u.ldap_login = s.owner_login AND u.active "
                "CROSS JOIN LATERAL (SELECT max(a.last_seen_at) AS seen FROM app.app_session a "
                "  WHERE a.ldap_login = s.owner_login AND a.revoked_at IS NULL "
                "  AND a.expires_at > now()) act "
                "WHERE s.kind = 'jira_session' AND act.seen IS NOT NULL "
                "ORDER BY act.seen DESC, s.owner_login"
            ).fetchall()
        return [Poller(r[0], r[1]) for r in rows]

    # --- sync_state ---

    def get_state(self, scope: str) -> SyncStateRow | None:
        table = _STATE_TABLES[scope]
        pool = self._pool_for(scope)
        extra = ", initial_load_done" if scope == SCOPE_NEEDS else ", true"
        with pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_STATE_COLS}{extra} FROM {table} WHERE scope = %s", (scope,)
            ).fetchone()
        return SyncStateRow(*row) if row else None

    def record_attempt_success(self, scope: str, window_started_at: datetime) -> None:
        table = _STATE_TABLES[scope]
        done = ", initial_load_done = true" if scope == SCOPE_NEEDS else ""
        with self._pool_for(scope).connection() as conn:
            conn.execute(
                f"UPDATE {table} SET last_sync_at = %s, last_success_at = now(), "
                f"last_attempt_at = now(), last_error = NULL, last_error_code = NULL, "
                f"last_error_at = NULL{done} WHERE scope = %s",
                (window_started_at, scope),
            )

    def record_attempt_failure(self, scope: str, code: str, message: str) -> None:
        table = _STATE_TABLES[scope]
        with self._pool_for(scope).connection() as conn:
            conn.execute(
                f"UPDATE {table} SET last_attempt_at = now(), last_error = %s, "
                f"last_error_code = %s, last_error_at = now() WHERE scope = %s",
                (message, code, scope),
            )

    def _pool_for(self, scope: str) -> ConnectionPool:
        if scope == SCOPE_EMPLOYEES:
            if self._cv is None:
                raise RuntimeError("пул cv_db не настроен (CV_DB_URL)")
            return self._cv
        return self._app

    # --- потребности ---

    def max_need_updated(self) -> datetime | None:
        with self._app.connection() as conn:
            return conn.execute("SELECT max(jira_updated_at) FROM app.jira_need").fetchone()[0]

    def upsert_needs(self, rows: list[NeedRow]) -> int:
        """Идемпотентный upsert по jira_key одной транзакцией; эпики, которых нет в кэше,
        создаются «заглушками» (name = epic_key). Возвращает число строк в пакете."""
        if not rows:
            return 0
        epics = sorted({r.epic_key for r in rows if r.epic_key})
        params = [
            {c: getattr(r, c) for c in _NEED_COLS.split(", ")} for r in rows
        ]
        with self._app.connection() as conn, conn.transaction():
            if epics:
                conn.cursor().executemany(
                    "INSERT INTO app.jira_epic (epic_key, name) VALUES (%s, %s) "
                    "ON CONFLICT (epic_key) DO NOTHING",
                    [(k, k) for k in epics],
                )
            conn.cursor().executemany(_NEED_UPSERT, params)
        return len(rows)

    def reconcile_need_employees(self, mapping: dict[str, str]) -> int:
        """Сверяет employee_key кэша потребностей с однозначной картой логин -> ключ карточки:
        проставляет найденное, сбрасывает ключи, которые больше не однозначны. Число изменённых."""
        logins, keys = list(mapping), list(mapping.values())
        with self._app.connection() as conn, conn.transaction():
            set_ = conn.execute(
                "UPDATE app.jira_need n SET employee_key = m.k "
                "FROM unnest(%s::text[], %s::text[]) AS m (l, k) "
                "WHERE n.employee_login = m.l AND n.employee_key IS DISTINCT FROM m.k",
                (logins, keys),
            ).rowcount
            cleared = conn.execute(
                "UPDATE app.jira_need SET employee_key = NULL WHERE employee_key IS NOT NULL "
                "AND (employee_login IS NULL OR NOT employee_login = ANY(%s::text[]))",
                (logins,),
            ).rowcount
        return set_ + cleared

    def list_needs(
        self, f: NeedFilter, *, limit: int, offset: int, sort: str = "created"
    ) -> tuple[list[dict[str, Any]], int]:
        where, params = _need_where(f)
        order = "n.jira_updated_at" if sort == "updated" else "n.jira_created_at"
        with self._app.connection() as conn:
            total = conn.execute(
                f"SELECT count(*) FROM app.jira_need n{where}", params
            ).fetchone()[0]
            cur = conn.execute(
                "SELECT n.jira_key, n.title, n.description, n.requirements_text, n.status, "
                "n.epic_key, e.name AS epic_name, n.employee_key, n.assignee, n.author, "
                "n.contract_type, n.rate_amount, n.rate_currency, n.rate_includes_vat, "
                "n.rate_unit, n.rate_proposer, n.jira_created_at, n.jira_updated_at, "
                "n.synced_at, "
                "COALESCE((SELECT array_agg(t.tag ORDER BY t.tag) FROM app.jira_need_tag t "
                "WHERE t.need_key = n.jira_key), '{}') AS tags "
                "FROM app.jira_need n LEFT JOIN app.jira_epic e ON e.epic_key = n.epic_key"
                f"{where} ORDER BY {order} DESC, n.jira_key DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            )
            cols = [d.name for d in cur.description]
            items = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        return items, total

    def count_needs(self) -> int:
        with self._app.connection() as conn:
            return conn.execute("SELECT count(*) FROM app.jira_need").fetchone()[0]

    def need_group_counts(self) -> dict[str, Any]:
        """Группы статусов (FR-035) со счётчиками; «Прочие» и «Все статусы» - виртуальные."""
        with self._app.connection() as conn:
            groups = conn.execute(
                "SELECT g.id, g.name, g.sort_order, count(n.jira_key) FROM app.status_group g "
                "LEFT JOIN app.status_group_status s ON s.group_id = g.id "
                "LEFT JOIN app.jira_need n ON n.status = s.status_name "
                "GROUP BY g.id ORDER BY g.sort_order, g.id"
            ).fetchall()
            total = conn.execute("SELECT count(*) FROM app.jira_need").fetchone()[0]
            other = conn.execute(
                "SELECT count(*) FROM app.jira_need "
                "WHERE status NOT IN (SELECT status_name FROM app.status_group_status)"
            ).fetchone()[0]
            statuses = conn.execute(
                "SELECT status, count(*) FROM app.jira_need GROUP BY status ORDER BY status"
            ).fetchall()
        return {"groups": groups, "total": total, "other": other, "statuses": statuses}

    # --- эпики ---

    def get_epic(self, epic_key: str) -> EpicRow | None:
        with self._app.connection() as conn:
            row = conn.execute(
                "SELECT epic_key, name, summary, rules_cache, rules_fetched_at "
                "FROM app.jira_epic WHERE epic_key = %s",
                (epic_key,),
            ).fetchone()
        if not row:
            return None
        rules = row[3]
        if isinstance(rules, str):
            rules = json.loads(rules)
        return EpicRow(row[0], row[1], row[2], rules, row[4])

    def save_epic(
        self, epic_key: str, name: str, summary: str | None, rules: list[dict[str, str]]
    ) -> None:
        with self._app.connection() as conn:
            conn.execute(
                "INSERT INTO app.jira_epic (epic_key, name, summary, rules_cache, "
                "rules_fetched_at) VALUES (%s, %s, %s, %s::jsonb, now()) "
                "ON CONFLICT (epic_key) DO UPDATE SET name = EXCLUDED.name, "
                "summary = EXCLUDED.summary, rules_cache = EXCLUDED.rules_cache, "
                "rules_fetched_at = now()",
                (epic_key, name, summary, json.dumps(rules, ensure_ascii=False)),
            )

    def count_epic_needs(self, epic_key: str) -> int:
        with self._app.connection() as conn:
            return conn.execute(
                "SELECT count(*) FROM app.jira_need WHERE epic_key = %s", (epic_key,)
            ).fetchone()[0]

    # --- сотрудники (cv_db) ---

    def employee_login_map(self) -> dict[str, str]:
        """Логин -> ключ карточки только там, где карточка с этим логином ровно одна."""
        with self._pool_for(SCOPE_EMPLOYEES).connection() as conn:
            rows = conn.execute(
                "SELECT jira_login, min(jira_key) FROM cv.employee WHERE jira_login IS NOT NULL "
                "GROUP BY jira_login HAVING count(*) = 1"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def upsert_employees(self, rows: list[EmployeeRow]) -> int:
        """Идемпотентный upsert; заметка и авторство заметки (FR-033) не затираются."""
        if not rows:
            return 0
        params = [
            (r.jira_key, r.full_name, r.status, r.assignable, r.extra_experience, r.jira_login,
             r.vacation_plan)
            for r in rows
        ]
        with self._pool_for(SCOPE_EMPLOYEES).connection() as conn, conn.transaction():
            conn.cursor().executemany(
                "INSERT INTO cv.employee (jira_key, full_name, status, assignable, "
                "extra_experience, jira_login, vacation_plan) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (jira_key) DO UPDATE SET full_name = EXCLUDED.full_name, "
                "status = EXCLUDED.status, assignable = EXCLUDED.assignable, "
                "extra_experience = EXCLUDED.extra_experience, jira_login = EXCLUDED.jira_login, "
                "vacation_plan = EXCLUDED.vacation_plan, synced_at = now()",
                params,
            )
        return len(rows)

    def list_employees(
        self, *, status: str | None, assignable: bool | None, query: str | None,
        limit: int, offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        conds: list[str] = []
        params: list[Any] = []
        if status:
            conds.append("status = %s")
            params.append(status)
        if assignable is not None:
            conds.append("assignable = %s")
            params.append(assignable)
        if query:
            conds.append("full_name ILIKE %s")
            params.append("%" + query.replace("\\", "\\\\").replace("%", "\\%")
                          .replace("_", "\\_") + "%")
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        with self._pool_for(SCOPE_EMPLOYEES).connection() as conn:
            total = conn.execute(f"SELECT count(*) FROM cv.employee{where}", params).fetchone()[0]
            cur = conn.execute(
                "SELECT jira_key, full_name, status, assignable, extra_experience, "
                "vacation_plan, note, note_author, note_updated_at, synced_at "
                "FROM cv.employee"
                f"{where} ORDER BY full_name, jira_key LIMIT %s OFFSET %s",
                [*params, limit, offset],
            )
            cols = [d.name for d in cur.description]
            items = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        return items, total

    def employee_status_counts(self) -> list[tuple[str, int]]:
        with self._pool_for(SCOPE_EMPLOYEES).connection() as conn:
            return conn.execute(
                "SELECT status, count(*) FROM cv.employee GROUP BY status ORDER BY status"
            ).fetchall()
