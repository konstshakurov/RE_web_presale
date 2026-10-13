"""Помощники тестов синхронизации Jira на стенде (KON-63, Pre.QA-7).

Только локальный стенд и фейк Jira (``qa/stand/fake_jira.py``); живая Jira не используется. Помощники
не оставляют на стенде «пауза»/«отказ Jira»: сброс - ``restore_stand`` в конце модулей.
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime

import httpx

from ._fakes import JIRA_URL, JiraCtl, db, wait_until
from ._stand import COMPOSE_PROJECT, Session, logged_in

FORBIDDEN_TEXT = "You do not have the permission to see the specified issue (fake Jira, QA)"
STAND_NEEDS_INTERVAL = 20.0
STAND_EMPLOYEES_INTERVAL = 30.0


def put_issue(key: str, *, status: str = "Требуется кандидат", summary: str = "Аналитик (QA KON-63)",
              project: str = "ANALYTICS", itype: str = "Аналитик", fields: dict | None = None,
              created_days_ago: float = 1) -> None:
    """Создать/заменить задачу в фейке Jira с произвольными полями (``POST /__qa/put``)."""
    httpx.post(JIRA_URL + "/__qa/put", timeout=15, json={
        "key": key, "status": status, "summary": summary, "project": project, "type": itype,
        "fields": fields or {}, "created_days_ago": created_days_ago}).raise_for_status()


def search_calls() -> int:
    """Сколько страниц поиска фейк Jira отдал с момента reset (опрос и «Обновить»)."""
    return int(JiraCtl().stats()["search"])


def stamps(s: Session) -> dict[str, dict]:
    r = s.http.get("/sync/status")
    assert r.status_code == 200, r.text
    return {x["scope"]: x for x in r.json()}


def refresh(s: Session, scope: str, *, attempts: int = 6):
    """``POST /sync/refresh``; если фоновый опрос держит блокировку (busy), повторяет."""
    r = None
    for _ in range(attempts):
        r = s.post("/sync/refresh", json={"scope": scope})
        if not (r.status_code == 200 and r.json().get("busy")):
            return r
        time.sleep(1.5)
    return r


def age_sec(stamp_iso: str) -> float:
    ts = datetime.fromisoformat(stamp_iso.replace("Z", "+00:00"))
    return (datetime.now(UTC) - ts).total_seconds()


def pause_everyone() -> Session:
    """Состояние «вышли все»: сессии всех пользователей, кроме qa-jirablock, отозваны (как logout), токена
    Jira у qa-jirablock нет (вход в Jira для него заблокирован) - он остаётся в системе, но опрос вести
    не может. Возвращает его сессию: через неё читаются ответы ``/sync/*``."""
    from ._stage1 import ensure_allow_list

    ensure_allow_list()
    me = logged_in("qa-jirablock")
    with db("app_db") as c:
        c.execute("UPDATE app.app_session SET revoked_at = now() "
                  "WHERE revoked_at IS NULL AND ldap_login <> 'qa-jirablock'")
        c.execute("DELETE FROM app.secret_store WHERE owner_login = 'qa-jirablock' AND kind = 'jira_session'")
    return me


def api_sync_events(since_epoch: float) -> list[dict]:
    """JSON-записи ``app.sync.service`` из логов api и worker стенда после метки времени (rates_dropped:
    «Обновить» исполняет api, фоновый опрос - worker)."""
    since = datetime.fromtimestamp(since_epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = []
    for svc in ("api", "worker"):
        out = subprocess.run(["docker", "logs", "--since", since, f"{COMPOSE_PROJECT}-{svc}-1"],
                             capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
        for line in (out.stdout + out.stderr).splitlines():
            if line.startswith("{") and "app.sync.service" in line:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    return events


def restore_stand(jira: JiraCtl) -> Session:
    """Фейк Jira в исходном состоянии, допущенные восстановлены, пользователь вошёл заново, кэш полный."""
    from ._stage1 import ensure_allow_list, prepare_cache

    jira.fail("none")
    ensure_allow_list()
    s = logged_in("qa-user")
    prepare_cache(s, jira)
    wait_until(lambda: stamps(s)["needs"]["state"] == "ok", timeout=60, interval=2, what="штамп ok")
    return s
