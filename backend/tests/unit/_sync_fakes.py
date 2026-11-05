"""Фейки для тестов синхронизации: репозиторий в памяти и порт Jira. Сеть и БД не нужны."""
from __future__ import annotations

import contextlib
from dataclasses import replace
from datetime import UTC, datetime

from app.sync.repo import EpicRow, Poller, SyncStateRow

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def issue(key="ANALYTICS-1", status="Требуется кандидат", *, updated="2026-10-09T15:00:00.000+0400",
          created="2026-10-01T10:00:00.000+0400", **fields):
    """Задача в формате REST v2 (только нужные поля)."""
    f = {"summary": f"Заявка {key}", "status": {"name": status}, "created": created,
         "updated": updated, **fields}
    return {"key": key, "fields": f}


class FakeSyncRepo:
    def __init__(self, with_cv: bool = True) -> None:
        self.pollers: list[Poller] = []
        self.states: dict[str, SyncStateRow] = {}
        self.needs: dict[str, object] = {}
        self.employees: dict[str, object] = {}
        self.epics: dict[str, EpicRow] = {}
        self.busy: set[str] = set()
        self.has_cv = with_cv
        self.upsert_calls = 0
        self.list_calls: list[dict] = []
        self.employee_calls: list[dict] = []
        self.dept_heads: list[dict] = []
        self.stub_epics: set[str] = set()
        self.epic_comments_store: dict[str, dict[str, dict]] = {}
        self.theses: dict[str, dict] = {}
        self.saved_theses: list[tuple] = []

    def set_poller(self, login: str, secret_updated_at: datetime = T0) -> None:
        self.pollers = [p for p in self.pollers if p.login != login]
        self.pollers.append(Poller(login, secret_updated_at))

    def list_pollers(self):
        return list(self.pollers)

    @contextlib.contextmanager
    def try_lock(self, scope):
        if scope in self.busy:
            yield False
        else:
            self.busy.add(scope)
            try:
                yield True
            finally:
                self.busy.discard(scope)

    def get_state(self, scope):
        empty = SyncStateRow(scope, None, None, None, None, None, None, False)
        return self.states.get(scope) or empty

    def record_attempt_success(self, scope, started):
        self.states[scope] = SyncStateRow(scope, started, T0, T0, None, None, None, True)

    def record_attempt_failure(self, scope, code, message):
        old = self.get_state(scope)
        self.states[scope] = SyncStateRow(
            scope, old.last_sync_at, old.last_success_at, T0, message, code, T0,
            old.initial_load_done)

    def list_needs(self, f, *, limit, offset, sort="created", order_dir="desc"):
        self.list_calls.append({"filter": f, "limit": limit, "offset": offset,
                                "sort": sort, "order_dir": order_dir})
        return [], 0

    def max_need_updated(self):
        return max((n.jira_updated_at for n in self.needs.values()), default=None)

    def upsert_needs(self, rows):
        self.upsert_calls += 1
        for r in rows:
            self.needs[r.jira_key] = r
        return len(rows)

    def employee_login_map(self):
        by: dict[str, list[str]] = {}
        for e in self.employees.values():
            if getattr(e, "jira_login", None):
                by.setdefault(e.jira_login, []).append(e.jira_key)
        return {k: v[0] for k, v in by.items() if len(v) == 1}

    def reconcile_need_employees(self, mapping):
        for k, n in list(self.needs.items()):
            key = mapping.get(n.employee_login) if n.employee_login else None
            self.needs[k] = replace(n, employee_key=key)
        return 0

    def upsert_employees(self, rows):
        for r in rows:
            self.employees[r.jira_key] = r
        return len(rows)

    def list_employees(self, **kw):
        self.employee_calls.append(kw)
        return [], 0

    def employee_status_counts(self):
        return []

    def list_dept_heads(self, hidden_logins=()):
        self.hidden_logins_seen = tuple(hidden_logins)
        return list(self.dept_heads)

    def get_epic(self, key):
        return self.epics.get(key)

    def save_epic(self, key, name, summary, rules):
        self.epics[key] = EpicRow(key, name, summary, rules, FIXED_NOW[0])

    def count_epic_needs(self, key, key_prefix=None):
        return sum(1 for n in self.needs.values()
                   if n.epic_key == key and (not key_prefix or n.jira_key.startswith(key_prefix)))

    # --- комментарии эпиков и тезисы (KON-160) ---

    def list_client_epics(self):
        return sorted({*self.epics, *self.stub_epics})

    def add_stub_epic(self, key):
        self.stub_epics.add(key)

    def upsert_epic_comments(self, epic_key, comments):
        store = self.epic_comments_store.setdefault(epic_key, {})
        for c in comments:
            store[str(c.get("id") or "")] = {
                "id": str(c.get("id") or ""), "author": c.get("author"),
                "updated": c.get("updated"), "body": c.get("body") or "",
            }
        return len(comments)

    def epic_comments(self, epic_key):
        return [
            {"id": k, "author": v["author"], "updated": v["updated"], "body": v["body"]}
            for k, v in sorted(self.epic_comments_store.get(epic_key, {}).items())
        ]

    def epic_comments_hash(self, epic_key):
        import hashlib

        store = self.epic_comments_store.get(epic_key, {})
        if not store:
            return None
        joined = "|".join(
            f"{k}:{v['updated'] or ''}:{len(v['body'])}" for k, v in sorted(store.items()))
        return hashlib.sha256(joined.encode()).hexdigest()

    def epic_theses(self, epic_key):
        return self.theses.get(epic_key)

    def save_epic_theses(self, epic_key, comments_hash, theses, provider_name):
        self.saved_theses.append((epic_key, comments_hash, theses, provider_name))
        self.theses[epic_key] = {
            "comments_hash": comments_hash, "theses": theses,
            "generated_at": T0.isoformat(), "provider_name": provider_name,
        }

    def epic_key_for_need(self, need_key):
        for n in self.needs.values():
            if n.jira_key == need_key:
                return n.epic_key
        return None

    def need_exists(self, need_key):
        return any(n.jira_key == need_key for n in self.needs.values())


FIXED_NOW = [T0]


class FakeJira:
    def __init__(self) -> None:
        self.issues: list[dict] = []
        self.error: Exception | None = None
        self.errors_by_token: dict[str, Exception] = {}
        self.calls: list[tuple] = []
        self.epic = {"name": "Клиент Альфа", "summary": "Эпик Альфа"}
        self.epic_issue = {"key": "SA-10", "fields": {"summary": "Эпик Альфа",
                                                       "customfield_14205": "Есть NDA"}}
        self.children: list[dict] = []
        self.comments_by_key: dict[str, list[dict]] = {}

    def add_comment(self, issue_key, cid, body, *, author="Клиентоса А.Б.",
                    updated="2026-10-01T10:00:00.000+0400"):
        self.comments_by_key.setdefault(issue_key, []).append({
            "id": cid, "author": author, "body": body,
            "created": updated, "updated": updated,
        })

    def search_incremental(self, token, since, *, project=None, issuetype=None, fields=None,
                           max_pages=200):
        self.calls.append(("search", token, since, project, issuetype, tuple(fields or ())))
        err = self.errors_by_token.get(token) or self.error
        if err:
            raise err
        return list(self.issues)

    def get_epic(self, token, key, *, fields=None):
        self.calls.append(("epic", token, key))
        if self.error:
            raise self.error
        return dict(self.epic)

    def get_issue(self, token, key, *, fields=None):
        self.calls.append(("issue", token, key))
        return self.epic_issue

    def get_epic_children(self, token, key, *, fields=None, max_pages=50):
        self.calls.append(("children", token, key))
        self.children_fields = tuple(fields or ())
        return list(self.children)

    def get_comments(self, token, issue_key):
        self.calls.append(("comments", token, issue_key))
        if self.error:
            raise self.error
        return list(self.comments_by_key.get(issue_key, []))


class FakeStore:
    """Вместо SecretStore: токен по логину."""

    def __init__(self, tokens: dict[str, str]) -> None:
        self.tokens = tokens

    def load_jira_session(self, login):
        return self.tokens.get(login)
