"""SQL к app.status_group / app.status_group_status (роль app_rw). Только плейсхолдеры."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from psycopg import errors

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

#: Итоги изменяющих операций
OK, NOT_FOUND, CONFLICT = "ok", "not_found", "conflict"


@dataclass(frozen=True)
class GroupRow:
    id: int
    name: str
    sort_order: int
    statuses: list[str]
    need_count: int  # потребностей кэша, попавших в группу


@dataclass(frozen=True)
class StatusRow:
    status: str
    count: int  # потребностей с этим статусом в кэше


class StatusGroupRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def list_groups(self) -> list[GroupRow]:
        with self._pool.connection() as conn:
            groups = conn.execute(
                "SELECT g.id, g.name, g.sort_order, "
                "COALESCE(array_agg(s.status_name ORDER BY s.status_name) "
                "  FILTER (WHERE s.status_name IS NOT NULL), '{}') "
                "FROM app.status_group g "
                "LEFT JOIN app.status_group_status s ON s.group_id = g.id "
                "GROUP BY g.id ORDER BY g.sort_order, g.id"
            ).fetchall()
            counts = conn.execute(
                "SELECT s.group_id, count(n.jira_key) FROM app.status_group_status s "
                "JOIN app.jira_need n ON n.status = s.status_name GROUP BY s.group_id"
            ).fetchall()
        by_group = dict(counts)
        return [GroupRow(g[0], g[1], g[2], list(g[3]), by_group.get(g[0], 0)) for g in groups]

    def get_group(self, group_id: int) -> GroupRow | None:
        return next((g for g in self.list_groups() if g.id == group_id), None)

    def cache_statuses(self) -> list[StatusRow]:
        """Статусы, встретившиеся в кэше потребностей, со счётчиками."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT status, count(*) FROM app.jira_need GROUP BY status ORDER BY status"
            ).fetchall()
        return [StatusRow(r[0], r[1]) for r in rows]

    def name_taken(self, name: str, *, except_id: int | None = None) -> bool:
        """Имя занято без учёта регистра (ограничение уникальности в БД чувствительно к нему)."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM app.status_group WHERE lower(name) = lower(%s) "
                "AND (%s::int IS NULL OR id <> %s::int)",
                (name, except_id, except_id),
            ).fetchone()
        return row is not None

    def create_group(self, name: str) -> int | None:
        """Новая группа в конец порядка; None - имя занято."""
        try:
            with self._pool.connection() as conn:
                row = conn.execute(
                    "INSERT INTO app.status_group (name, sort_order) VALUES "
                    "(%s, COALESCE((SELECT max(sort_order) FROM app.status_group), 0) + 1) "
                    "RETURNING id",
                    (name,),
                ).fetchone()
        except errors.UniqueViolation:
            return None
        return row[0]

    def update_group(
        self, group_id: int, *, name: str | None = None, sort_order: int | None = None
    ) -> str:
        try:
            with self._pool.connection() as conn:
                cur = conn.execute(
                    "UPDATE app.status_group SET name = COALESCE(%s, name), "
                    "sort_order = COALESCE(%s, sort_order) WHERE id = %s",
                    (name, sort_order, group_id),
                )
        except errors.UniqueViolation:
            return CONFLICT
        return OK if cur.rowcount else NOT_FOUND

    def delete_group(self, group_id: int) -> bool:
        """Состав удаляется каскадом (FK ON DELETE CASCADE)."""
        with self._pool.connection() as conn:
            return conn.execute(
                "DELETE FROM app.status_group WHERE id = %s", (group_id,)
            ).rowcount == 1

    def add_status(self, group_id: int, status: str) -> str:
        """Идемпотентно: статус уже в группе - без изменений."""
        try:
            with self._pool.connection() as conn:
                conn.execute(
                    "INSERT INTO app.status_group_status (group_id, status_name) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (group_id, status),
                )
        except errors.ForeignKeyViolation:
            return NOT_FOUND
        return OK

    def remove_status(self, group_id: int, status: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM app.status_group_status WHERE group_id = %s AND status_name = %s",
                (group_id, status),
            )

    def set_order(self, ids: list[int]) -> bool:
        """sort_order = позиция в списке (с 1). Список содержит все группы ровно по разу."""
        with self._pool.connection() as conn, conn.transaction():
            existing = {r[0] for r in conn.execute("SELECT id FROM app.status_group").fetchall()}
            if set(ids) != existing or len(ids) != len(existing):
                return False
            conn.cursor().executemany(
                "UPDATE app.status_group SET sort_order = %s WHERE id = %s",
                [(pos, gid) for pos, gid in enumerate(ids, start=1)],
            )
        return True
