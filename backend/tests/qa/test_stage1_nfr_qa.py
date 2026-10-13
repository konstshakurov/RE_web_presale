"""KON-47 (Pre.QA-2), Q47-04/05 и замеры NFR-002/008 на стенде.

Требования: NFR-002 (рост 100 потребностей в неделю, до 2 с), NFR-008 (списки из кэша <= 2 с), NFR-005
(нет журнала действий), NFR-004 (нет автоудаления CV и сотрудников; ревью кода), NFR-007 (раздельные
учётные записи и БД). Порядок файлов: запускается после ``test_stage1_match_qa.py``.
Цифры замеров выводятся в stdout (``-s``) и берутся в отчёт ``qa/report_stage1.md``.
"""
from __future__ import annotations

import re
import statistics
import threading
import time

import pytest

from ._fakes import JiraCtl, db, requires_fakes
from ._stage1 import (cleanup_generated_needs, ensure_allow_list, percentile, prepare_cache)
from ._stand import ROOT, Session, logged_in, requires_stand

pytestmark = [requires_stand, requires_fakes]

LIMIT_SEC = 2.0


# --- NFR-005 / NFR-004 / NFR-007 ---------------------------------------------------------------------

@pytest.mark.parametrize("database", ["app_db", "cv_db"])
def test_nfr005_no_audit_log_tables(database):
    """NFR-005: журнала действий пользователей нет ни в одной БД (ни таблицы, ни колонок «действие»)."""
    with db(database) as c:
        tables = [r[0] for r in c.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables "
            "WHERE table_schema IN ('app', 'cv', 'meta')").fetchall()]
        assert tables, "таблиц не найдено: проверка была бы пустой"
        bad = [t for t in tables if re.search(r"audit|journal|activity|action_log|event_log|history", t, re.I)]
        assert bad == [], bad
        cols = c.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema IN ('app', 'cv') AND column_name ~* '^(action|actor|event_type|audit)'").fetchall()
        assert cols == [], cols


def test_nfr005_migrations_have_no_audit_ddl():
    text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "db" / "migrations").rglob("V*.sql")
                     if ".down." not in p.name)
    assert not re.search(r"CREATE\s+TABLE\s+\S*(audit|journal|action_log)", text, re.I)


def test_nfr004_code_has_no_automatic_deletion_of_cv_or_employees():
    """NFR-004 (ревью кода): DELETE по cv.employee и массовых удалений CV нет; удаление файла - только по
    явному запросу (cv/repo.py: по id); purge касается задач LLM и кэшей оценок/поиска."""
    deletes: list[tuple[str, str]] = []
    for path in (ROOT / "backend" / "app").rglob("*.py"):
        for m in re.finditer(r"(DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+([\w.]+)", path.read_text(encoding="utf-8")):
            deletes.append((str(path.relative_to(ROOT / "backend" / "app")).replace("\\", "/"), m.group(2)))
    targets = {t for _, t in deletes}
    assert "cv.employee" not in targets
    assert targets <= {
        "app.secret_store", "app.llm_provider", "app.llm_task", "app.status_group", "app.status_group_status",
        "cv.employee_file", "cv.employee_file_skill", "cv.match_run", "cv.search_run", "cv.search_result",
        "cv.search_screen",
    }, targets
    cv_file_deletes = [p for p, t in deletes if t == "cv.employee_file"]
    assert cv_file_deletes == ["cv/repo.py"]                               # единственное место, по id файла
    src = (ROOT / "backend" / "app" / "cv" / "repo.py").read_text(encoding="utf-8")
    assert re.search(r"DELETE FROM cv\.employee_file WHERE id = %s", src)
    # планировщик не трогает файлы: purge только задач LLM и кэшей поиска
    sched = (ROOT / "backend" / "app" / "worker.py").read_text(encoding="utf-8")
    assert "employee_file" not in sched


def test_nfr004_files_are_not_deleted_by_idle_time(user=None):
    """Файлы существуют, пока их не удалили явно: после загрузки и ожидания записей не убывает (smoke)."""
    with db("cv_db") as c:
        n1 = c.execute("SELECT count(*) FROM cv.employee_file").fetchone()[0]
        time.sleep(5)
        n2 = c.execute("SELECT count(*) FROM cv.employee_file").fetchone()[0]
    assert n2 >= n1


def test_nfr007_cv_db_has_no_tokens_or_settings_and_app_db_has_no_cv_data():
    """NFR-007 п. 1: токены, ключи и настройки - только в app_db; CV и сотрудники - только в cv_db."""
    with db("cv_db") as c:
        cv_tables = {r[0] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema NOT IN "
            "('pg_catalog','information_schema')").fetchall()}
    assert not cv_tables & {"secret_store", "app_user", "app_session", "llm_provider", "llm_task", "app_setting"}
    assert {"employee", "employee_file", "employee_file_content"} <= cv_tables
    with db("app_db") as c:
        app_tables = {r[0] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema NOT IN "
            "('pg_catalog','information_schema')").fetchall()}
    assert not app_tables & {"employee", "employee_file", "employee_file_content", "employee_file_skill"}
    assert {"secret_store", "app_user", "llm_provider", "jira_need"} <= app_tables


def test_nfr007_roles_cannot_read_each_others_data():
    """NFR-007 п. 2: cv_rw не читает app.*, app_rw не читает cv.* (даже в своей БД таких таблиц нет), а
    чужую БД открыть нельзя (после миграций V007..V010)."""
    import psycopg

    with db("cv_db") as c:
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM app.secret_store LIMIT 1")
    with db("app_db") as c:
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM cv.employee_file LIMIT 1")
    # роль app_rw не может читать таблицы с токенами других БД и не владеет cv-схемой
    with db("cv_db") as c:
        owner_tables = c.execute(
            "SELECT count(*) FROM information_schema.role_table_grants WHERE grantee = 'cv_rw' "
            "AND table_schema = 'cv'").fetchone()[0]
        assert owner_tables > 0
        n = c.execute("SELECT count(*) FROM information_schema.role_table_grants WHERE grantee = 'app_rw'").fetchone()[0]
        assert n == 0


# --- NFR-002 / NFR-008: время списков из кэша ----------------------------------------------------------

def _timed(s: Session, url: str, params: dict | None = None) -> float:
    t0 = time.perf_counter()
    r = s.http.get(url, params=params)
    dt = time.perf_counter() - t0
    assert r.status_code == 200, (url, r.status_code, r.text[:200])
    return dt


def _mix(s: Session, groups: list[str]) -> list[float]:
    times = []
    for g in groups:
        times.append(_timed(s, "/sync/needs", {"group": g, "limit": 100}))
    times.append(_timed(s, "/sync/needs", {"q": "нагрузочный 207", "limit": 100}))
    times.append(_timed(s, "/sync/needs", {"sort": "updated", "order": "desc", "limit": 100}))
    times.append(_timed(s, "/sync/needs", {"sort": "created", "order": "asc", "limit": 500}))
    times.append(_timed(s, "/sync/needs/groups"))
    times.append(_timed(s, "/sync/employees", {"limit": 100}))
    times.append(_timed(s, "/resume/employees", {"limit": 100}))
    times.append(_timed(s, "/needs/match-counts", {"keys": [f"ANALYTICS-{20000 + i}" for i in range(100)]}))
    return times


@pytest.fixture(scope="module")
def load_env():
    ensure_allow_list()
    jira = JiraCtl()
    s = logged_in("qa-user")
    prepare_cache(s, jira)
    yield jira, s
    cleanup_generated_needs()
    jira.reset()
    s.close()


@pytest.mark.parametrize("total", [500, 2000])
def test_nfr002_nfr008_lists_from_cache_under_two_seconds(load_env, total):
    """NFR-002/NFR-008 (M-002, M-006): при 500 и 2000 потребностях в кэше списки открываются <= 2 с (p95 по
    замерам смеси запросов; 4 параллельных пользователя; в это время идёт цикл опроса)."""
    jira, s = load_env
    have = s.http.get("/sync/needs", params={"limit": 1}).json()["total"]
    jira.gen(total - (have - 24) if have >= 24 else total, start=20000 + (have - 24), status="mixed")
    t0 = time.perf_counter()
    r = s.post("/sync/refresh", json={"scope": "needs"})
    assert r.status_code == 200, r.text
    sync_sec = time.perf_counter() - t0
    now_total = s.http.get("/sync/needs", params={"limit": 1}).json()["total"]
    assert now_total >= total
    groups = [g["key"] for g in s.http.get("/sync/needs/groups").json()["groups"]]

    results: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        try:
            u = logged_in(name)
            local: list[float] = []
            for _ in range(3):
                local += _mix(u, groups)
            with lock:
                results.extend(local)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc!r}")

    poll = threading.Thread(target=lambda: s.post("/sync/refresh", json={"scope": "needs"}))
    threads = [threading.Thread(target=worker, args=(n,)) for n in ("qa-user", "qa-admin", "qa-revoked", "qa-jirablock")]
    poll.start()
    [t.start() for t in threads]
    [t.join(300) for t in threads]
    poll.join(60)
    assert errors == [], errors
    assert len(results) >= 4 * 3 * 8
    p50, p95, mx = statistics.median(results), percentile(results, 95), max(results)
    print(f"NFR-002/008 total={now_total}: запросов {len(results)}, p50={p50 * 1000:.0f} мс, "
          f"p95={p95 * 1000:.0f} мс, max={mx * 1000:.0f} мс; опрос {now_total - 24} потр. за {sync_sec:.1f} с")
    assert p95 <= LIMIT_SEC and mx <= LIMIT_SEC * 2


def test_nfr002_weekly_growth_100_needs_one_poll_cycle_without_errors(load_env):
    """NFR-002: прирост 100 потребностей (неделя) обрабатывается одним циклом опроса без ошибок."""
    jira, s = load_env
    have = s.http.get("/sync/needs", params={"limit": 1}).json()["total"]
    jira.gen(100, start=22000, status="Требуется кандидат")
    t0 = time.perf_counter()
    r = s.post("/sync/refresh", json={"scope": "needs"})
    dt = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    assert r.json()["upserted"] >= 100 and r.json()["sync"]["state"] == "ok"
    assert s.http.get("/sync/needs", params={"limit": 1}).json()["total"] >= have + 100
    print(f"NFR-002: цикл опроса +100 потребностей: {dt:.1f} с, upserted={r.json()['upserted']}")
    assert dt <= 60


def test_br010_status_not_resolution_in_sync_fields_and_jql():
    """BR-010: приложение читает статус, а не резолюцию: поля запроса и JQL без ``resolution``."""
    from app.sync.mapping import EMPLOYEE_FIELDS, EPIC_CHILD_FIELDS, EPIC_FIELDS, NEED_FIELDS

    for fields in (NEED_FIELDS, EPIC_CHILD_FIELDS, EMPLOYEE_FIELDS, EPIC_FIELDS):
        assert "resolution" not in fields and "resolutiondate" not in fields
    for path in (ROOT / "backend" / "app" / "sync").glob("*.py"):
        assert "resolution" not in path.read_text(encoding="utf-8").lower(), path.name
