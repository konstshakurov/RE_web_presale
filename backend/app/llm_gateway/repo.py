"""SQL к app.llm_provider, app.llm_task и источникам словарей app_db (роль app_rw).

Только плейсхолдеры. Открытых секретов и текстов запросов здесь нет: payload/result приходят
и уходят шифртекстом (``SealedSecret``), ключи провайдеров лежат в secret_store (модуль secrets).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from psycopg import Rollback, errors

from ..secrets.crypto import SealedSecret

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

#: Итоги изменяющих операций
OK, NOT_FOUND, CONFLICT, IN_USE, IS_DEFAULT = "ok", "not_found", "conflict", "in_use", "is_default"

#: Статусы задачи
AWAITING, QUEUED, RUNNING, DONE, FAILED, TIMEOUT, CANCELLED = (
    "awaiting_confirmation", "queued", "running", "done", "failed", "timeout", "cancelled",
)
FINAL = (DONE, FAILED, TIMEOUT, CANCELLED)


@dataclass(frozen=True)
class ProviderRow:
    id: int
    name: str
    url: str
    is_external: bool
    is_default: bool
    timeout_sec: int
    model: str
    has_key: bool
    updated_at: datetime


@dataclass(frozen=True)
class TaskRow:
    id: str
    owner_login: str
    purpose: str
    ref: str | None
    provider_id: int
    is_external: bool
    status: str
    payload: SealedSecret | None
    result: SealedSecret | None
    error_code: str | None
    error_message: str | None
    attempts: int
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


_PROVIDER = (
    "p.id, p.name, p.url, p.is_external, p.is_default, p.timeout_sec, p.model, "
    "EXISTS (SELECT 1 FROM app.secret_store s WHERE s.kind = 'llm_key' AND s.provider_id = p.id), "
    "p.updated_at"
)
_TASK = (
    "id::text, owner_login, purpose, ref, provider_id, is_external, status, "
    "payload_ct, payload_nonce, result_ct, result_nonce, key_id, error_code, error_message, "
    "attempts, created_at, updated_at, confirmed_at, started_at, finished_at"
)


def _sealed(ct, nonce, key_id) -> SealedSecret | None:
    if ct is None or nonce is None:
        return None
    return SealedSecret(ciphertext=bytes(ct), nonce=bytes(nonce), key_id=key_id)


def _task(r) -> TaskRow:
    return TaskRow(
        id=r[0], owner_login=r[1], purpose=r[2], ref=r[3], provider_id=r[4], is_external=r[5],
        status=r[6], payload=_sealed(r[7], r[8], r[11]), result=_sealed(r[9], r[10], r[11]),
        error_code=r[12], error_message=r[13], attempts=r[14], created_at=r[15],
        updated_at=r[16], confirmed_at=r[17], started_at=r[18], finished_at=r[19],
    )


class LlmRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # --- провайдеры (FR-030) ---

    def list_providers(self) -> list[ProviderRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_PROVIDER} FROM app.llm_provider p "
                "ORDER BY p.is_default DESC, p.name"
            ).fetchall()
        return [ProviderRow(*r) for r in rows]

    def get_provider(self, provider_id: int) -> ProviderRow | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_PROVIDER} FROM app.llm_provider p WHERE p.id = %s", (provider_id,)
            ).fetchone()
        return ProviderRow(*row) if row else None

    def get_default_provider(self) -> ProviderRow | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_PROVIDER} FROM app.llm_provider p WHERE p.is_default"
            ).fetchone()
        return ProviderRow(*row) if row else None

    def count_providers(self) -> int:
        with self._pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM app.llm_provider").fetchone()[0]

    def create_provider(
        self, name: str, url: str, *, is_external: bool, is_default: bool,
        timeout_sec: int, model: str,
    ) -> int | None:
        """id нового провайдера или None, если имя занято."""
        with self._pool.connection() as conn:
            if is_default:
                conn.execute("UPDATE app.llm_provider SET is_default = false WHERE is_default")
            row = conn.execute(
                "INSERT INTO app.llm_provider "
                "(name, url, is_external, is_default, timeout_sec, model) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (name) DO NOTHING RETURNING id",
                (name, url, is_external, is_default, timeout_sec, model),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            return row[0]

    def update_provider(self, provider_id: int, fields: dict) -> str:
        """Частичное изменение полей; is_default принимается только как true."""
        allowed = {"name", "url", "is_external", "timeout_sec", "model"}
        sets = [f"{k} = %s" for k in fields if k in allowed]
        vals = [v for k, v in fields.items() if k in allowed]
        try:
            with self._pool.connection() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM app.llm_provider WHERE id = %s FOR UPDATE", (provider_id,)
                ).fetchone()
                if exists is None:
                    return NOT_FOUND
                if fields.get("is_default") is True:
                    conn.execute(
                        "UPDATE app.llm_provider SET is_default = false "
                        "WHERE is_default AND id <> %s", (provider_id,))
                    sets.append("is_default = true")
                if sets:
                    conn.execute(
                        f"UPDATE app.llm_provider SET {', '.join(sets)}, updated_at = now() "
                        "WHERE id = %s", (*vals, provider_id))
        except errors.UniqueViolation:
            return CONFLICT
        return OK

    def delete_provider(self, provider_id: int) -> str:
        """Нельзя удалить провайдера по умолчанию и провайдера с задачами (история)."""
        try:
            with self._pool.connection() as conn:
                row = conn.execute(
                    "SELECT is_default FROM app.llm_provider WHERE id = %s FOR UPDATE",
                    (provider_id,),
                ).fetchone()
                if row is None:
                    return NOT_FOUND
                if row[0]:
                    return IS_DEFAULT
                conn.execute(
                    "DELETE FROM app.secret_store WHERE kind = 'llm_key' AND provider_id = %s",
                    (provider_id,))
                conn.execute("DELETE FROM app.llm_provider WHERE id = %s", (provider_id,))
        except errors.ForeignKeyViolation:
            return IN_USE
        return OK

    # --- словари обезличивания (источники app_db) ---

    def dictionary_sources(self) -> dict[str, list[str]]:
        """Имена клиентов (эпики Jira) и ФИО пользователей приложения."""
        with self._pool.connection() as conn:
            epics = conn.execute("SELECT name, summary FROM app.jira_epic").fetchall()
            users = conn.execute(
                "SELECT display_name FROM app.app_user WHERE display_name IS NOT NULL"
            ).fetchall()
        companies = [r[0] for r in epics if r[0]] + [r[1] for r in epics if r[1]]
        return {"companies": companies, "persons": [r[0] for r in users]}

    def get_setting(self, key: str) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM app.app_setting WHERE key = %s", (key,)
            ).fetchone()
        return row[0] if row else None

    def put_setting(self, key: str, value: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO app.app_setting (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (key, value),
            )

    # --- задачи (ADR-007, NFR-010) ---

    def create_task(
        self, *, task_id: str, owner_login: str, purpose: str, ref: str | None,
        provider_id: int, is_external: bool, status: str, payload: SealedSecret,
    ) -> TaskRow:
        """``task_id`` задаёт вызывающий: он входит в AAD шифртекста payload."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO app.llm_task (id, owner_login, purpose, ref, provider_id, "
                "is_external, status, payload_ct, payload_nonce, key_id) "
                f"VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_TASK}",
                (task_id, owner_login, purpose, ref, provider_id, is_external, status,
                 payload.ciphertext, payload.nonce, payload.key_id),
            ).fetchone()
        return _task(row)

    def get_task(self, task_id: str) -> TaskRow | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_TASK} FROM app.llm_task WHERE id = %s::uuid", (task_id,)
            ).fetchone()
        return _task(row) if row else None

    def list_tasks(self, owner_login: str, limit: int = 50) -> list[TaskRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_TASK} FROM app.llm_task WHERE owner_login = %s "
                "ORDER BY created_at DESC LIMIT %s", (owner_login, limit)
            ).fetchall()
        return [_task(r) for r in rows]

    def confirm_task(self, task_id: str) -> bool:
        """awaiting_confirmation -> queued; False, если задача не ждёт подтверждения."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE app.llm_task SET status = 'queued', confirmed_at = now(), "
                "updated_at = now() WHERE id = %s::uuid AND status = 'awaiting_confirmation'",
                (task_id,),
            )
            return cur.rowcount == 1

    def update_payload(self, task_id: str, payload: SealedSecret) -> bool:
        """Замена шифртекста payload (правка текста пользователем): только пока задача ждёт
        подтверждения и не подтверждена; условие в самом UPDATE - гонки с confirm нет."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE app.llm_task SET payload_ct = %s, payload_nonce = %s, key_id = %s, "
                "updated_at = now() WHERE id = %s::uuid AND status = 'awaiting_confirmation' "
                "AND confirmed_at IS NULL AND payload_ct IS NOT NULL",
                (payload.ciphertext, payload.nonce, payload.key_id, task_id),
            )
            return cur.rowcount == 1

    def confirm_tasks(self, task_ids: list[str]) -> bool:
        """Групповое подтверждение: все задачи awaiting_confirmation -> queued одной транзакцией.
        Если хотя бы одна уже не ждёт подтверждения - не меняется ни одна (False)."""
        ids = sorted(set(task_ids))
        if not ids:
            return False
        ok = False
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                "UPDATE app.llm_task SET status = 'queued', confirmed_at = now(), "
                "updated_at = now() WHERE id = ANY(%s::uuid[]) "
                "AND status = 'awaiting_confirmation' RETURNING id",
                (ids,),
            ).fetchall()
            if len(rows) != len(ids):
                raise Rollback  # выход из блока откатывает транзакцию без исключения наружу
            ok = True
        return ok

    def cancel_task(self, task_id: str) -> bool:
        """Отмена не завершённой задачи; текст и подстановки удаляются."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE app.llm_task SET status = 'cancelled', payload_ct = NULL, "
                "payload_nonce = NULL, finished_at = now(), updated_at = now() "
                "WHERE id = %s::uuid AND status IN "
                "('awaiting_confirmation', 'queued', 'running')", (task_id,),
            )
            return cur.rowcount == 1

    def requeue_task(self, task_id: str) -> bool:
        """Повтор после таймаута/ошибки: failed|timeout -> queued (payload сохранён)."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE app.llm_task SET status = 'queued', error_code = NULL, "
                "error_message = NULL, finished_at = NULL, updated_at = now() "
                "WHERE id = %s::uuid AND status IN ('failed', 'timeout') "
                "AND payload_ct IS NOT NULL", (task_id,),
            )
            return cur.rowcount == 1

    def claim_next(self) -> TaskRow | None:
        """Взять старейшую задачу queued (SKIP LOCKED: несколько worker не пересекутся)."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "UPDATE app.llm_task SET status = 'running', started_at = now(), "
                "attempts = attempts + 1, updated_at = now() WHERE id = ("
                "  SELECT id FROM app.llm_task WHERE status = 'queued' "
                "  ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) "
                f"RETURNING {_TASK}"
            ).fetchone()
        return _task(row) if row else None

    def finish_ok(self, task_id: str, result: SealedSecret) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE app.llm_task SET status = 'done', result_ct = %s, result_nonce = %s, "
                "payload_ct = NULL, payload_nonce = NULL, error_code = NULL, "
                "error_message = NULL, finished_at = now(), updated_at = now() "
                "WHERE id = %s::uuid AND status = 'running'",
                (result.ciphertext, result.nonce, task_id),
            )
            return cur.rowcount == 1

    def finish_error(self, task_id: str, status: str, code: str, message: str) -> bool:
        """failed | timeout; payload остаётся для повтора (кнопка «Повторить»)."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE app.llm_task SET status = %s, error_code = %s, error_message = %s, "
                "finished_at = now(), updated_at = now() "
                "WHERE id = %s::uuid AND status = 'running'",
                (status, code, message[:500], task_id),
            )
            return cur.rowcount == 1

    def fail_stale_running(self, grace_sec: int = 30) -> int:
        """running дольше таймаута провайдера + запас: worker упал посреди вызова."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE app.llm_task t SET status = 'failed', error_code = 'worker_lost', "
                "error_message = 'Обработка прервана, повторите запрос', "
                "finished_at = now(), updated_at = now() FROM app.llm_provider p "
                "WHERE t.provider_id = p.id AND t.status = 'running' "
                "AND t.started_at < now() - make_interval(secs => p.timeout_sec + %s)",
                (grace_sec,),
            )
            return cur.rowcount

    def purge_finished(self, older_than_days: int) -> int:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM app.llm_task WHERE status IN ('done', 'failed', 'timeout', "
                "'cancelled') AND finished_at < now() - make_interval(days => %s)",
                (older_than_days,),
            )
            return cur.rowcount
