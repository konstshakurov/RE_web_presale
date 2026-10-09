"""Параметры синхронизации Jira (FR-028, ADR-004): из env, умолчания - допущения."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SyncSettings:
    needs_interval_sec: float = 300.0  # потребности: 5 мин
    employees_interval_sec: float = 3600.0  # сотрудники: 1 час
    pause_recheck_sec: float = 15.0  # опрос приостановлен: как скоро заметить новый вход
    epic_ttl_sec: float = 3600.0  # эпики и правила клиента: 1 час
    initial_load_days: int = 183  # первичная загрузка: 6 месяцев
    overlap_sec: float = 60.0  # пересечение окна опроса (ADR-004)
    jira_utc_offset_hours: float = 4.0  # ДОПУЩЕНИЕ: JQL-время читается в поясе Jira (+04:00)
    need_project: str = "ANALYTICS"
    need_issuetype: str = "Аналитик"
    employee_project: str = "ANALYTICS"
    employee_issuetype: str = "Epic"
    jira_base_url: str = ""
    heartbeat_path: str = "/tmp/worker.heartbeat"  # пульс для healthcheck compose


def _num(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} должен быть числом") from None
    if value <= 0:
        raise ValueError(f"{name} должен быть больше нуля")
    return value


def load_sync_settings(env: Mapping[str, str] | None = None) -> SyncSettings:
    e = os.environ if env is None else env
    d = SyncSettings()

    def text(name: str, default: str) -> str:
        return (e.get(name) or "").strip() or default

    offset_raw = (e.get("JIRA_UTC_OFFSET_HOURS") or "").strip()
    try:
        offset = float(offset_raw) if offset_raw else d.jira_utc_offset_hours
    except ValueError:
        raise ValueError("JIRA_UTC_OFFSET_HOURS должен быть числом") from None

    return SyncSettings(
        needs_interval_sec=_num(e, "SYNC_NEEDS_INTERVAL_SEC", d.needs_interval_sec),
        employees_interval_sec=_num(e, "SYNC_EMPLOYEES_INTERVAL_SEC", d.employees_interval_sec),
        pause_recheck_sec=_num(e, "SYNC_PAUSE_RECHECK_SEC", d.pause_recheck_sec),
        epic_ttl_sec=_num(e, "SYNC_EPIC_TTL_SEC", d.epic_ttl_sec),
        initial_load_days=int(_num(e, "SYNC_INITIAL_LOAD_DAYS", d.initial_load_days)),
        overlap_sec=_num(e, "SYNC_OVERLAP_SEC", d.overlap_sec),
        jira_utc_offset_hours=offset,
        need_project=text("JIRA_NEED_PROJECT", d.need_project),
        need_issuetype=text("JIRA_NEED_ISSUETYPE", d.need_issuetype),
        employee_project=text("JIRA_EMPLOYEE_PROJECT", d.employee_project),
        employee_issuetype=text("JIRA_EMPLOYEE_ISSUETYPE", d.employee_issuetype),
        jira_base_url=(e.get("JIRA_BASE_URL") or "").strip().rstrip("/"),
        heartbeat_path=text("WORKER_HEARTBEAT_PATH", d.heartbeat_path),
    )
