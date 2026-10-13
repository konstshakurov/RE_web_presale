"""SQL модуля matching: cv_db (роль cv_rw, отдельный пул, NFR-007) и чтение потребности из кэша
app_db (роль app_rw). Параметры только плейсхолдеры. Тексты CV и заметки в логи не пишутся.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.sync.mapping import NOT_ASSIGNABLE_STATUSES

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

PENDING, OK, FAILED = "pending", "ok", "failed"
DONE = "done"
#: Ступень пакета: скрининг по навыкам | глубокая оценка по тексту CV
STAGE_SCREEN, STAGE_DEEP = "screen", "deep"
#: Ступень 2 после скрининга: ждёт скрининга | то же без учёта кэша | решение принято
CHAIN_PENDING, CHAIN_FORCE, CHAIN_DONE = "pending", "pending_force", "done"
#: Предел «зависшего» claim без задачи (процесс упал между claim и submit), секунды
CLAIM_STALE_SEC = 120


@dataclass(frozen=True)
class NeedRow:
    jira_key: str
    title: str
    description: str | None
    requirements_text: str | None
    status: str
    jira_updated_at: datetime
    tags: tuple[str, ...]


@dataclass(frozen=True)
class CandRow:
    """Сотрудник с актуальным основным CV, из которого извлечён текст."""
    employee_key: str
    full_name: str
    status: str
    assignable: bool
    note: str | None
    note_author: str | None
    note_updated_at: datetime | None
    extra_experience: str | None
    file_id: int
    file_name: str
    skills: tuple[str, ...]


@dataclass(frozen=True)
class EmployeeCard:
    employee_key: str
    full_name: str
    status: str
    assignable: bool
    extra_experience: str | None
    vacation_plan: str | None
    note: str | None
    note_author: str | None
    note_updated_at: datetime | None
    synced_at: datetime


@dataclass(frozen=True)
class ResumeRow:
    """Строка раздела «Резюме» (FR-032): карточка + основное CV + навыки."""
    employee_key: str
    full_name: str
    status: str
    assignable: bool
    extra_experience: str | None
    vacation_plan: str | None
    note: str | None
    note_author: str | None
    note_updated_at: datetime | None
    file_id: int | None
    file_name: str | None
    extraction_status: str | None
    skills: tuple[str, ...]
    skills_status: str | None  # pending | ok | failed | None - не извлекались


@dataclass(frozen=True)
class SkillState:
    file_id: int
    status: str
    task_id: str | None
    model: str | None
    requested_by: str
    error_code: str | None
    error_message: str | None
    extracted_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class RecRow:
    employee_key: str
    cv_file_id: int
    model: str
    match_percent: int
    rationale: str
    need_jira_updated_at: datetime
    note_snapshot_at: datetime | None
    created_at: datetime
    gaps: list[str] | None = None  # NULL - не рассчитано, [] - полное совпадение (cv_db V010)


@dataclass(frozen=True)
class NewRec:
    employee_key: str
    cv_file_id: int
    match_percent: int
    rationale: str
    note_snapshot_at: datetime | None
    gaps: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RunRow:
    need_key: str
    requested_by: str
    provider_id: int | None
    model: str
    need_jira_updated_at: datetime
    created_at: datetime
    chain_state: str = CHAIN_DONE


@dataclass(frozen=True)
class BatchRow:
    id: int
    need_key: str
    task_id: str
    employee_keys: tuple[str, ...]
    file_ids: tuple[int, ...]
    status: str
    error_code: str | None
    error_message: str | None
    stage: str = STAGE_DEEP
    run_id: str = ""


@dataclass(frozen=True)
class NewBatch:
    task_id: str
    employee_keys: tuple[str, ...]
    file_ids: tuple[int, ...]
    stage: str = STAGE_DEEP
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class NewScreen:
    """Предварительный балл скрининга; ``skills_hash`` - набор навыков, по которому он получен."""
    employee_key: str
    cv_file_id: int
    match_percent: int
    skills_hash: str


@dataclass(frozen=True)
class ScreenRow:
    employee_key: str
    cv_file_id: int
    model: str
    match_percent: int
    skills_hash: str
    need_jira_updated_at: datetime | None  # у поиска нет потребности
    created_at: datetime


@dataclass(frozen=True)
class SearchRunRow:
    """Прогон «Поиска по смыслу» пользователя по запросу (FR-032)."""
    requested_by: str
    query_hash: str
    query_text: str
    provider_id: int | None
    model: str
    created_at: datetime
    chain_state: str = CHAIN_DONE
    scope: dict | None = None  # фильтры запуска: ступень 2 берёт ту же область поиска


@dataclass(frozen=True)
class SearchBatchRow:
    id: int
    requested_by: str
    query_hash: str
    task_id: str
    employee_keys: tuple[str, ...]
    file_ids: tuple[int, ...]
    status: str
    error_code: str | None
    error_message: str | None
    stage: str = STAGE_DEEP
    run_id: str = ""


@dataclass(frozen=True)
class SearchResRow:
    employee_key: str
    cv_file_id: int
    model: str
    match_percent: int
    rationale: str
    note_snapshot_at: datetime | None
    created_at: datetime
    gaps: list[str] | None = None


def _gaps_json(gaps: Sequence[str] | None) -> str | None:
    """gaps -> jsonb: None остаётся SQL NULL («не рассчитано»), () - пустой массив."""
    return None if gaps is None else json.dumps(list(gaps), ensure_ascii=False)


_CAND = (
    "e.jira_key, e.full_name, e.status, e.assignable, e.note, e.note_author, "
    "e.note_updated_at, e.extra_experience, f.id, f.file_name, "
    "COALESCE((SELECT array_agg(s.tag ORDER BY s.tag) FROM cv.employee_file_skill s "
    "WHERE s.file_id = f.id), '{}')"
)
_MAIN_JOIN = (
    "JOIN cv.employee_file f ON f.employee_key = e.jira_key AND f.kind = 'main_cv' "
    "AND NOT f.is_archived"
)
_STATE = "file_id, status, task_id::text, model, requested_by, error_code, error_message, " \
         "extracted_at, updated_at"
_BATCH = ("id, need_key, task_id::text, employee_keys, file_ids, status, error_code, "
          "error_message, stage, run_id::text")
_RUN = "need_key, requested_by, provider_id, model, need_jira_updated_at, created_at, chain_state"
_SRUN = ("requested_by, query_hash, query_text, provider_id, model, created_at, chain_state, "
         "scope")
_SBATCH = ("id, requested_by, query_hash, task_id::text, employee_keys, file_ids, status, "
           "error_code, error_message, stage, run_id::text")


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _batch(r) -> BatchRow:
    return BatchRow(r[0], r[1], r[2], tuple(r[3]), tuple(r[4]), r[5], r[6], r[7], r[8], r[9])


def _sbatch(r) -> SearchBatchRow:
    return SearchBatchRow(
        r[0], r[1], r[2], r[3], tuple(r[4]), tuple(r[5]), r[6], r[7], r[8], r[9], r[10])


class NeedSource:
    """Потребность из кэша Jira (app_db)."""

    def __init__(self, app_pool: ConnectionPool) -> None:
        self._pool = app_pool

    def get_need(self, key: str) -> NeedRow | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                "SELECT jira_key, title, description, requirements_text, status, jira_updated_at, "
                "COALESCE((SELECT array_agg(t.tag ORDER BY t.tag) FROM app.jira_need_tag t "
                "WHERE t.need_key = n.jira_key), '{}') "
                "FROM app.jira_need n WHERE jira_key = %s", (key,),
            ).fetchone()
        return NeedRow(*r[:6], tuple(r[6])) if r else None


class RunTx:
    """Операции над прогоном внутри транзакции с advisory-lock по потребности."""

    def __init__(self, conn, need_key: str) -> None:
        self._conn = conn
        self._need = need_key

    def run(self) -> RunRow | None:
        r = self._conn.execute(
            f"SELECT {_RUN} FROM cv.match_run WHERE need_key = %s", (self._need,)).fetchone()
        return RunRow(*r) if r else None

    def batches(self) -> list[BatchRow]:
        rows = self._conn.execute(
            f"SELECT {_BATCH} FROM cv.match_batch WHERE need_key = %s ORDER BY id",
            (self._need,)).fetchall()
        return [_batch(r) for r in rows]

    def replace(self, run: RunRow, batches: Sequence[NewBatch]) -> None:
        """Прогон по потребности один: прежний (с пакетами, каскадом) заменяется."""
        c = self._conn
        c.execute("DELETE FROM cv.match_run WHERE need_key = %s", (run.need_key,))
        c.execute(
            "INSERT INTO cv.match_run (need_key, requested_by, provider_id, model, "
            "need_jira_updated_at, chain_state) VALUES (%s, %s, %s, %s, %s, %s)",
            (run.need_key, run.requested_by, run.provider_id, run.model,
             run.need_jira_updated_at, run.chain_state),
        )
        for b in batches:
            c.execute(
                "INSERT INTO cv.match_batch (need_key, task_id, employee_keys, file_ids, stage, "
                "run_id) VALUES (%s, %s::uuid, %s::text[], %s::bigint[], %s, %s::uuid)",
                (run.need_key, b.task_id, list(b.employee_keys), list(b.file_ids), b.stage,
                 b.run_id),
            )

    def finish_chain(self) -> None:
        """Решение о ступени 2 принято (отправлять нечего либо ступень не нужна)."""
        self._conn.execute(
            "UPDATE cv.match_run SET chain_state = 'done' WHERE need_key = %s", (self._need,))


class SearchTx:
    """Операции над прогоном поиска внутри транзакции с advisory-lock по (пользователь, запрос)."""

    def __init__(self, conn, login: str, query_hash: str) -> None:
        self._conn = conn
        self._login = login
        self._hash = query_hash

    def run(self) -> SearchRunRow | None:
        r = self._conn.execute(
            f"SELECT {_SRUN} FROM cv.search_run WHERE requested_by = %s AND query_hash = %s",
            (self._login, self._hash)).fetchone()
        return SearchRunRow(*r) if r else None

    def batches(self) -> list[SearchBatchRow]:
        rows = self._conn.execute(
            f"SELECT {_SBATCH} FROM cv.search_batch WHERE requested_by = %s AND query_hash = %s "
            "ORDER BY id", (self._login, self._hash)).fetchall()
        return [_sbatch(r) for r in rows]

    def finish_chain(self) -> None:
        self._conn.execute(
            "UPDATE cv.search_run SET chain_state = 'done' WHERE requested_by = %s "
            "AND query_hash = %s", (self._login, self._hash))

    def replace(self, run: SearchRunRow, batches: Sequence[NewBatch]) -> None:
        """Прогон по (пользователь, запрос) один: прежний (с пакетами, каскадом) заменяется."""
        c = self._conn
        c.execute("DELETE FROM cv.search_run WHERE requested_by = %s AND query_hash = %s",
                  (run.requested_by, run.query_hash))
        c.execute(
            "INSERT INTO cv.search_run (requested_by, query_hash, query_text, provider_id, model, "
            "chain_state, scope) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
            (run.requested_by, run.query_hash, run.query_text, run.provider_id, run.model,
             run.chain_state, json.dumps(run.scope or {}, ensure_ascii=False)))
        for b in batches:
            c.execute(
                "INSERT INTO cv.search_batch (requested_by, query_hash, task_id, employee_keys, "
                "file_ids, stage, run_id) VALUES (%s, %s, %s::uuid, %s::text[], %s::bigint[], "
                "%s, %s::uuid)",
                (run.requested_by, run.query_hash, b.task_id, list(b.employee_keys),
                 list(b.file_ids), b.stage, b.run_id))


class MatchRepo:
    def __init__(self, cv_pool: ConnectionPool) -> None:
        self._pool = cv_pool

    # --- кандидаты и карточки ---

    def list_candidates(self, *, only_assignable: bool = True) -> list[CandRow]:
        """Сотрудники с актуальным основным CV и извлечённым текстом (FR-005). Отбор идёт по
        самому статусу: все, кроме NOT_ASSIGNABLE_STATUSES (FR-002); сохранённый флаг
        cv.employee.assignable не используется - он мог разойтись со статусом."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_CAND} FROM cv.employee e {_MAIN_JOIN} "
                "AND f.extraction_status = 'ok' "
                "WHERE (NOT %s OR e.status <> ALL(%s::text[])) ORDER BY e.full_name, e.jira_key",
                (only_assignable, sorted(NOT_ASSIGNABLE_STATUSES)),
            ).fetchall()
        return [CandRow(*r[:-1], tuple(r[-1])) for r in rows]

    def get_main_cv(self, employee_key: str) -> tuple[int, str] | None:
        """(id файла, извлечённый текст) актуального основного CV; None - нет файла или
        текст не извлечён."""
        with self._pool.connection() as conn:
            r = conn.execute(
                "SELECT id, extracted_text FROM cv.employee_file WHERE employee_key = %s "
                "AND kind = 'main_cv' AND NOT is_archived AND extraction_status = 'ok'",
                (employee_key,),
            ).fetchone()
        return (r[0], r[1] or "") if r else None

    def get_cv_texts(self, file_ids: Sequence[int]) -> dict[int, str]:
        if not file_ids:
            return {}
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, extracted_text FROM cv.employee_file WHERE id = ANY(%s::bigint[]) "
                "AND extraction_status = 'ok'", (list(file_ids),),
            ).fetchall()
        return {r[0]: r[1] or "" for r in rows}

    def get_employee(self, employee_key: str) -> EmployeeCard | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                "SELECT jira_key, full_name, status, assignable, extra_experience, vacation_plan, "
                "note, note_author, note_updated_at, synced_at FROM cv.employee "
                "WHERE jira_key = %s", (employee_key,),
            ).fetchone()
        return EmployeeCard(*r) if r else None

    def get_skills(self, file_id: int) -> tuple[str, ...]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT tag FROM cv.employee_file_skill WHERE file_id = %s ORDER BY tag",
                (file_id,)).fetchall()
        return tuple(r[0] for r in rows)

    def get_skills_many(self, file_ids: Sequence[int]) -> dict[int, tuple[str, ...]]:
        if not file_ids:
            return {}
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT file_id, array_agg(tag ORDER BY tag) FROM cv.employee_file_skill "
                "WHERE file_id = ANY(%s::bigint[]) GROUP BY file_id", (list(file_ids),),
            ).fetchall()
        return {r[0]: tuple(r[1]) for r in rows}

    # --- «Резюме»: фильтры (FR-032) ---

    def list_resume(
        self, *, query: str | None, status: str | None, tags: Sequence[str], match_all: bool,
        limit: int, offset: int,
    ) -> tuple[list[ResumeRow], int]:
        conds = ["TRUE"]
        params: list[Any] = []
        if status:
            conds.append("e.status = %s")
            params.append(status)
        if tags:
            if match_all:
                conds.append(
                    "(SELECT count(DISTINCT s.tag) FROM cv.employee_file_skill s "
                    "WHERE s.file_id = f.id AND s.tag = ANY(%s::text[])) = %s")
                params += [list(tags), len(set(tags))]
            else:
                conds.append(
                    "EXISTS (SELECT 1 FROM cv.employee_file_skill s "
                    "WHERE s.file_id = f.id AND s.tag = ANY(%s::text[]))")
                params.append(list(tags))
        if query:
            like = _like(query)
            conds.append(
                "(e.full_name ILIKE %s OR e.jira_key ILIKE %s OR EXISTS ("
                "SELECT 1 FROM cv.employee_file ff WHERE ff.employee_key = e.jira_key "
                "AND NOT ff.is_archived AND (ff.file_name ILIKE %s "
                "OR ff.extracted_text ILIKE %s)))")
            params += [like, like, like, like]
        where = " AND ".join(conds)
        frm = (
            "FROM cv.employee e LEFT JOIN cv.employee_file f ON f.employee_key = e.jira_key "
            "AND f.kind = 'main_cv' AND NOT f.is_archived "
            "LEFT JOIN cv.file_skill_state st ON st.file_id = f.id"
        )
        with self._pool.connection() as conn:
            total = conn.execute(f"SELECT count(*) {frm} WHERE {where}", params).fetchone()[0]
            rows = conn.execute(
                "SELECT e.jira_key, e.full_name, e.status, e.assignable, e.extra_experience, "
                "e.vacation_plan, e.note, e.note_author, e.note_updated_at, f.id, f.file_name, "
                "f.extraction_status, "
                "COALESCE((SELECT array_agg(s.tag ORDER BY s.tag) FROM cv.employee_file_skill s "
                "WHERE s.file_id = f.id), '{}'), st.status "
                f"{frm} WHERE {where} ORDER BY e.full_name, e.jira_key LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        out = [ResumeRow(*r[:12], tuple(r[12]), r[13]) for r in rows]
        return out, total

    def list_technologies(self, limit: int = 500) -> list[tuple[str, int]]:
        """Подсказки фильтра «Технология»: теги актуальных основных CV и число сотрудников."""
        with self._pool.connection() as conn:
            return conn.execute(
                "SELECT s.tag, count(*) FROM cv.employee_file_skill s "
                "JOIN cv.employee_file f ON f.id = s.file_id AND f.kind = 'main_cv' "
                "AND NOT f.is_archived GROUP BY s.tag ORDER BY count(*) DESC, s.tag LIMIT %s",
                (limit,),
            ).fetchall()

    # --- навыки (FR-027) ---

    def get_skill_state(self, file_id: int) -> SkillState | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                f"SELECT {_STATE} FROM cv.file_skill_state WHERE file_id = %s", (file_id,)
            ).fetchone()
        return SkillState(*r) if r else None

    def state_by_task(self, task_id: str) -> SkillState | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                f"SELECT {_STATE} FROM cv.file_skill_state WHERE task_id = %s::uuid", (task_id,)
            ).fetchone()
        return SkillState(*r) if r else None

    def pending_skill_states(self, limit: int = 100) -> list[SkillState]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_STATE} FROM cv.file_skill_state WHERE status = 'pending' "
                "AND task_id IS NOT NULL ORDER BY updated_at LIMIT %s", (limit,)).fetchall()
        return [SkillState(*r) for r in rows]

    def claim_skills(self, file_id: int, requested_by: str, model: str) -> bool:
        """Занять извлечение навыков файла. True - можно ставить задачу: состояния не было,
        прежняя попытка упала либо зависла без задачи. Извлечённое (ok) и идущее (pending с
        задачей) не занимается - навыки не пересчитываются без замены файла (FR-027)."""
        with self._pool.connection() as conn:
            r = conn.execute(
                "INSERT INTO cv.file_skill_state (file_id, status, requested_by, model) "
                "VALUES (%s, 'pending', %s, %s) ON CONFLICT (file_id) DO UPDATE SET "
                "status = 'pending', task_id = NULL, requested_by = EXCLUDED.requested_by, "
                "model = EXCLUDED.model, error_code = NULL, error_message = NULL, "
                "updated_at = now() "
                "WHERE cv.file_skill_state.status = 'failed' OR (cv.file_skill_state.status = "
                "'pending' AND cv.file_skill_state.task_id IS NULL AND "
                "cv.file_skill_state.updated_at < now() - make_interval(secs => %s)) "
                "RETURNING file_id",
                (file_id, requested_by, model, CLAIM_STALE_SEC),
            ).fetchone()
        return r is not None

    def set_skills_task(self, file_id: int, task_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE cv.file_skill_state SET task_id = %s::uuid, updated_at = now() "
                "WHERE file_id = %s AND status = 'pending'", (task_id, file_id))

    def finish_skills_ok(self, file_id: int, tags: Sequence[str]) -> bool:
        """Теги пишутся одной транзакцией вместе со статусом; повтор безопасен."""
        with self._pool.connection() as conn, conn.transaction():
            cur = conn.execute(
                "UPDATE cv.file_skill_state SET status = 'ok', extracted_at = now(), "
                "error_code = NULL, error_message = NULL, updated_at = now() "
                "WHERE file_id = %s AND status = 'pending'", (file_id,))
            if cur.rowcount == 0:
                return False
            conn.execute("DELETE FROM cv.employee_file_skill WHERE file_id = %s", (file_id,))
            conn.cursor().executemany(
                "INSERT INTO cv.employee_file_skill (file_id, tag) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING", [(file_id, t) for t in tags])
        return True

    def finish_skills_failed(self, file_id: int, code: str, message: str) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE cv.file_skill_state SET status = 'failed', error_code = %s, "
                "error_message = %s, updated_at = now() WHERE file_id = %s AND status = 'pending'",
                (code, message[:500], file_id))
        return cur.rowcount > 0

    # --- рекомендации (FR-005) ---

    def upsert_recommendations(
        self, need_key: str, recs: Sequence[NewRec], *, model: str, need_updated_at: datetime,
    ) -> int:
        """Кэш по связке потребность + основное CV + модель (cv.recommendation_cache_uq).
        Строки, чей файл уже удалён из БД, пропускаются (FK и join)."""
        if not recs:
            return 0
        n = 0
        with self._pool.connection() as conn, conn.transaction():
            for r in recs:
                cur = conn.execute(
                    "INSERT INTO cv.recommendation (need_key, employee_key, cv_file_id, "
                    "match_percent, rationale, model, need_jira_updated_at, note_snapshot_at, "
                    "gaps) SELECT %s, f.employee_key, f.id, %s, %s, %s, %s, %s, %s::jsonb "
                    "FROM cv.employee_file f "
                    "WHERE f.id = %s AND f.employee_key = %s AND f.kind = 'main_cv' "
                    "ON CONFLICT (need_key, cv_file_id, model) DO UPDATE SET "
                    "match_percent = EXCLUDED.match_percent, rationale = EXCLUDED.rationale, "
                    "need_jira_updated_at = EXCLUDED.need_jira_updated_at, "
                    "note_snapshot_at = EXCLUDED.note_snapshot_at, gaps = EXCLUDED.gaps, "
                    "created_at = now()",
                    (need_key, r.match_percent, r.rationale, model, need_updated_at,
                     r.note_snapshot_at, _gaps_json(r.gaps), r.cv_file_id, r.employee_key),
                )
                n += cur.rowcount
        return n

    def list_recommendations(self, need_key: str) -> list[RecRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT employee_key, cv_file_id, model, match_percent, rationale, "
                "need_jira_updated_at, note_snapshot_at, created_at, gaps FROM cv.recommendation "
                "WHERE need_key = %s ORDER BY created_at DESC, id DESC", (need_key,)).fetchall()
        return [RecRow(*r) for r in rows]

    def recommended_pairs(self, need_keys: Sequence[str]) -> dict[str, set[tuple[str, int]]]:
        """Пары (сотрудник, файл CV) с сохранённой оценкой по каждой потребности из списка."""
        if not need_keys:
            return {}
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT need_key, employee_key, cv_file_id FROM cv.recommendation "
                "WHERE need_key = ANY(%s)", (list(need_keys),)).fetchall()
        out: dict[str, set[tuple[str, int]]] = {}
        for need_key, employee_key, file_id in rows:
            out.setdefault(need_key, set()).add((employee_key, file_id))
        return out

    # --- прогоны ---

    @contextmanager
    def run_tx(self, need_key: str) -> Iterator[RunTx]:
        """Транзакция с advisory-lock по потребности: два «Сопоставить» подряд не создадут
        двух прогонов. Откат при исключении."""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("match:" + need_key,))
            yield RunTx(conn, need_key)

    def get_run(self, need_key: str) -> RunRow | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                f"SELECT {_RUN} FROM cv.match_run WHERE need_key = %s", (need_key,)).fetchone()
        return RunRow(*r) if r else None

    def list_batches(self, need_key: str) -> list[BatchRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_BATCH} FROM cv.match_batch WHERE need_key = %s ORDER BY id",
                (need_key,)).fetchall()
        return [_batch(r) for r in rows]

    def batch_by_task(self, task_id: str) -> BatchRow | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                f"SELECT {_BATCH} FROM cv.match_batch WHERE task_id = %s::uuid", (task_id,)
            ).fetchone()
        return _batch(r) if r else None

    def batches_by_run(self, run_id: str) -> list[BatchRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_BATCH} FROM cv.match_batch WHERE run_id = %s::uuid ORDER BY id",
                (run_id,)).fetchall()
        return [_batch(r) for r in rows]

    def list_match_screens(self, need_key: str) -> list[ScreenRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT employee_key, cv_file_id, model, match_percent, skills_hash, "
                "need_jira_updated_at, created_at FROM cv.match_screen WHERE need_key = %s "
                "ORDER BY created_at DESC", (need_key,)).fetchall()
        return [ScreenRow(*r) for r in rows]

    def upsert_match_screens(
        self, need_key: str, rows: Sequence[NewScreen], *, model: str, need_updated_at: datetime,
    ) -> int:
        """Кэш скрининга по связке потребность + основное CV + модель. Файлы, которых уже нет,
        пропускаются."""
        n = 0
        if not rows:
            return n
        with self._pool.connection() as conn, conn.transaction():
            for r in rows:
                cur = conn.execute(
                    "INSERT INTO cv.match_screen (need_key, cv_file_id, employee_key, model, "
                    "match_percent, need_jira_updated_at, skills_hash) "
                    "SELECT %s, f.id, f.employee_key, %s, %s, %s, %s FROM cv.employee_file f "
                    "WHERE f.id = %s AND f.employee_key = %s AND f.kind = 'main_cv' "
                    "ON CONFLICT (need_key, cv_file_id, model) DO UPDATE SET "
                    "match_percent = EXCLUDED.match_percent, "
                    "need_jira_updated_at = EXCLUDED.need_jira_updated_at, "
                    "skills_hash = EXCLUDED.skills_hash, created_at = now()",
                    (need_key, model, r.match_percent, need_updated_at, r.skills_hash,
                     r.cv_file_id, r.employee_key))
                n += cur.rowcount
        return n

    def pending_batches(self, limit: int = 200) -> list[BatchRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_BATCH} FROM cv.match_batch WHERE status = 'pending' ORDER BY id "
                "LIMIT %s", (limit,)).fetchall()
        return [_batch(r) for r in rows]

    def finish_batch(self, batch_id: int, status: str, code: str | None = None,
                     message: str | None = None) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE cv.match_batch SET status = %s, error_code = %s, error_message = %s, "
                "finished_at = now() WHERE id = %s AND status = 'pending'",
                (status, code, (message or "")[:500] or None, batch_id))
        return cur.rowcount > 0

    # --- «Поиск по смыслу» (FR-032) ---

    @contextmanager
    def search_tx(self, login: str, query_hash: str) -> Iterator[SearchTx]:
        """Как ``run_tx``: два «Применить» подряд не создадут двух прогонов."""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                         (f"search:{login}:{query_hash}",))
            yield SearchTx(conn, login, query_hash)

    def get_search_run(self, login: str, query_hash: str) -> SearchRunRow | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                f"SELECT {_SRUN} FROM cv.search_run WHERE requested_by = %s AND query_hash = %s",
                (login, query_hash)).fetchone()
        return SearchRunRow(*r) if r else None

    def list_search_batches(self, login: str, query_hash: str) -> list[SearchBatchRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_SBATCH} FROM cv.search_batch WHERE requested_by = %s "
                "AND query_hash = %s ORDER BY id", (login, query_hash)).fetchall()
        return [_sbatch(r) for r in rows]

    def search_batches_by_run(self, run_id: str) -> list[SearchBatchRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_SBATCH} FROM cv.search_batch WHERE run_id = %s::uuid ORDER BY id",
                (run_id,)).fetchall()
        return [_sbatch(r) for r in rows]

    def list_search_screens(self, query_hash: str) -> list[ScreenRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT employee_key, cv_file_id, model, match_percent, skills_hash, "
                "NULL::timestamptz, created_at FROM cv.search_screen WHERE query_hash = %s "
                "ORDER BY created_at DESC", (query_hash,)).fetchall()
        return [ScreenRow(*r) for r in rows]

    def upsert_search_screens(
        self, query_hash: str, rows: Sequence[NewScreen], *, model: str,
    ) -> int:
        n = 0
        if not rows:
            return n
        with self._pool.connection() as conn, conn.transaction():
            for r in rows:
                cur = conn.execute(
                    "INSERT INTO cv.search_screen (query_hash, cv_file_id, employee_key, model, "
                    "match_percent, skills_hash) "
                    "SELECT %s, f.id, f.employee_key, %s, %s, %s FROM cv.employee_file f "
                    "WHERE f.id = %s AND f.employee_key = %s AND f.kind = 'main_cv' "
                    "ON CONFLICT (query_hash, cv_file_id, model) DO UPDATE SET "
                    "match_percent = EXCLUDED.match_percent, skills_hash = EXCLUDED.skills_hash, "
                    "created_at = now()",
                    (query_hash, model, r.match_percent, r.skills_hash, r.cv_file_id,
                     r.employee_key))
                n += cur.rowcount
        return n

    def search_batch_by_task(self, task_id: str) -> SearchBatchRow | None:
        with self._pool.connection() as conn:
            r = conn.execute(
                f"SELECT {_SBATCH} FROM cv.search_batch WHERE task_id = %s::uuid", (task_id,)
            ).fetchone()
        return _sbatch(r) if r else None

    def finish_search_batch(self, batch_id: int, status: str, code: str | None = None,
                            message: str | None = None) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE cv.search_batch SET status = %s, error_code = %s, error_message = %s, "
                "finished_at = now() WHERE id = %s AND status = 'pending'",
                (status, code, (message or "")[:500] or None, batch_id))
        return cur.rowcount > 0

    def upsert_search_results(
        self, query_hash: str, recs: Sequence[NewRec], *, model: str,
    ) -> int:
        """Кэш по связке запрос + основное CV + модель. Файлы, которых уже нет, пропускаются."""
        if not recs:
            return 0
        n = 0
        with self._pool.connection() as conn, conn.transaction():
            for r in recs:
                cur = conn.execute(
                    "INSERT INTO cv.search_result (query_hash, cv_file_id, employee_key, model, "
                    "match_percent, rationale, note_snapshot_at, gaps) "
                    "SELECT %s, f.id, f.employee_key, %s, %s, %s, %s, %s::jsonb "
                    "FROM cv.employee_file f "
                    "WHERE f.id = %s AND f.employee_key = %s AND f.kind = 'main_cv' "
                    "ON CONFLICT (query_hash, cv_file_id, model) DO UPDATE SET "
                    "match_percent = EXCLUDED.match_percent, rationale = EXCLUDED.rationale, "
                    "note_snapshot_at = EXCLUDED.note_snapshot_at, gaps = EXCLUDED.gaps, "
                    "created_at = now()",
                    (query_hash, model, r.match_percent, r.rationale, r.note_snapshot_at,
                     _gaps_json(r.gaps), r.cv_file_id, r.employee_key),
                )
                n += cur.rowcount
        return n

    def list_search_results(self, query_hash: str) -> list[SearchResRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT employee_key, cv_file_id, model, match_percent, rationale, "
                "note_snapshot_at, created_at, gaps FROM cv.search_result WHERE query_hash = %s "
                "ORDER BY created_at DESC", (query_hash,)).fetchall()
        return [SearchResRow(*r) for r in rows]

    def purge_search(self, retention_days: int) -> None:
        """Запросы и оценки старше срока хранения удаляются."""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM cv.search_run WHERE created_at < now() - "
                         "make_interval(days => %s)", (retention_days,))
            conn.execute("DELETE FROM cv.search_result WHERE created_at < now() - "
                         "make_interval(days => %s)", (retention_days,))
            conn.execute("DELETE FROM cv.search_screen WHERE created_at < now() - "
                         "make_interval(days => %s)", (retention_days,))
