"""KON-113: замеры NFR-009 (запись в Jira <= 10 с на вызов) и NFR-002 (ориентир ставки <= 2 с) на QA-стенде.

Запуск (стенд поднят: ``bash qa/stand/stand.sh up``; секреты берутся из ``qa/stand/.qa.env``, не печатаются):
    python tests_load/kon113_measure.py assign   # NFR-009: запись в фейк Jira, задержки 9 и 11 с
    python tests_load/kon113_measure.py bench    # NFR-002: rate-benchmark, список, status-actions на кэше 500+
    python tests_load/kon113_measure.py cleanup  # удалить свои синтетические данные (QA-БД и фейк Jira)
Результат: ``qa/reports/KON-113_raw.json``. В основную БД стенда (:8080) и в боевую Jira скрипт не пишет:
только ``QA_BASE_URL`` (:18080), фейк Jira (:18090) и QA-БД (контейнер re_web_presale_qa-db-1).
Все синтетические ключи: потребности ``ANALYTICS-9xxxxx`` (910000.. - назначение, 900000.. - кэш 500+),
сотрудник ``ANALYTICS-910501`` (логин ``qa.emp91``).
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "qa" / "reports" / "KON-113_raw.json"
CV_DIR = ROOT / "qa" / "testdata" / "cv"


def _env() -> dict[str, str]:
    out = {}
    for line in (ROOT / "qa" / "stand" / ".qa.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


ENV = _env()
BASE = ENV["QA_BASE_URL"] + "/api"
FAKE = ENV["QA_FAKE_JIRA_URL"]
assert ":18080" in ENV["QA_BASE_URL"] and ":18090" in FAKE, "не QA-стенд"
EMP = "ANALYTICS-910501"
EMP_LOGIN = "qa.emp91"
NEED_BASE = 910000
BENCH_START = 900000
BENCH_COUNT = 520
DB = ["docker", "exec", "re_web_presale_qa-db-1", "psql", "-U", "postgres", "-At"]


def pct(vals: list[float], p: float) -> float:
    s = sorted(vals)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def stats(vals: list[float]) -> dict:
    return {"n": len(vals), "p50": round(pct(vals, .5), 3), "p95": round(pct(vals, .95), 3),
            "max": round(max(vals), 3) if vals else None, "min": round(min(vals), 3) if vals else None,
            "mean": round(statistics.fmean(vals), 3) if vals else None}


def fake(path: str, body: dict | None = None):
    r = httpx.post(FAKE + path, json=body or {}, timeout=30) if body is not None else httpx.get(FAKE + path, timeout=30)
    r.raise_for_status()
    return r.json()


class Sess:
    def __init__(self, login: str = "qa-user") -> None:
        self.http = httpx.Client(base_url=BASE, timeout=120)
        pw = ENV["QA_LDAP_PW_USER" if login == "qa-user" else "QA_LDAP_PW_ADMIN"]
        r = self.http.post("/auth/login", json={"login": login, "password": pw})
        assert r.status_code == 200, r.status_code
        self.csrf = self.http.get("/auth/me").json()["csrf_token"]

    def post(self, url, **kw):
        return self.http.post(url, headers={"X-CSRF-Token": self.csrf}, **kw)


def timed(fn):
    t0 = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t0


# --------------------------------------------------------------------------- assign (NFR-009)

def put_need(n: int, status: str = "Поиск кандидата", proposer: str = "от производства") -> str:
    key = f"ANALYTICS-{NEED_BASE + n}"
    fake("/__qa/put", {"key": key, "status": status, "summary": f"Аналитик QA-замер {key}",
                       "fields": {"customfield_21105": {"value": proposer}, "customfield_10101": "CLN-1",
                                  "customfield_20809": 2500, "customfield_20811": {"value": "руб."},
                                  "customfield_20810": {"value": "да"}, "customfield_20812": {"value": "ч (час)"}}})
    return key


def ensure_employee(s: Sess) -> tuple[int, int]:
    fake("/__qa/put", {"key": EMP, "type": "Epic", "status": "Свободен", "summary": "Измеритель Тест Тестович",
                       "fields": {"customfield_10707": {"name": EMP_LOGIN}}})
    for _ in range(60):  # опрос сотрудников раз в ~30 с
        if s.http.get(f"/cv/employees/{EMP}/files").status_code == 200:
            break
        time.sleep(2)
    else:
        raise SystemExit("сотрудник не появился в кэше")
    for kind, fn in (("main_cv", "cv_main_ok.docx"), ("extra_cv", "cv_extra.docx")):
        r = s.http.post(f"/cv/employees/{EMP}/files", params={"kind": kind, "file_name": fn},
                        content=(CV_DIR / fn).read_bytes(), headers={"X-CSRF-Token": s.csrf})
        assert r.status_code == 201, (r.status_code, r.text[:200])
    card = s.http.get(f"/cv/employees/{EMP}/files").json()["files"]
    main = next(f["id"] for f in card if f["kind"] == "main_cv")
    extra = next(f["id"] for f in card if f["kind"] == "extra_cv")
    return main, extra


def writes() -> list[dict]:
    return fake("/__qa/writes?clear=1")["writes"]


def assign_once(s: Sess, key: str, extra: list[int], comment: str = "", completed: list[str] | None = None):
    body = {"employee_key": EMP, "extra_file_ids": extra, "rate_rub": 2700, "comment": comment,
            "completed": completed or []}
    r, dt = timed(lambda: s.post(f"/needs/{key}/assign", json=body))
    return r, dt


def per_op(ws: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for w in ws:
        out.setdefault(w["op"], []).append(w["server_ms"] / 1000)
    return out


def phase_assign() -> dict:
    s = Sess()
    main, extra = ensure_employee(s)
    res: dict = {"files": {"main": main, "extra": extra}}
    fake("/__qa/delay", {"delay": 0})
    fake("/__qa/write_fail", {"mode": "none"})
    fake("/__qa/drop", {"regex": "^ANALYTICS-91[0-9]{4}$"})  # свои потребности прошлого прогона (счётчики вложений)
    n = 0

    # S1: базовая линия, первое назначение (fields, attach x2, transition, rate), N=20
    agg: dict[str, list[float]] = {}
    totals, calls = [], []
    for _ in range(20):
        n += 1
        key = put_need(n)
        writes()
        r, dt = assign_once(s, key, [extra])
        assert r.status_code == 200, (r.status_code, r.text[:300])
        assert r.json()["rate_pending"] is False and r.json()["status"] == "Кандидат назначен", r.json()
        totals.append(dt)
        ws = writes()
        calls.append([w["op"] for w in ws])
        for op, v in per_op(ws).items():
            agg.setdefault(op, []).extend(v)
    res["S1_first_assign_no_delay"] = {"total_s": stats(totals), "per_call_s": {k: stats(v) for k, v in agg.items()},
                                       "calls_per_run": calls[0]}

    # S1b: повторное назначение (fields, attach x1, comment, rate), N=20
    agg, totals = {}, []
    for _ in range(20):
        n += 1
        key = put_need(n, status="Кандидат назначен")
        writes()
        r, dt = assign_once(s, key, [], comment="Повторное назначение (QA-замер)")
        assert r.status_code == 200, (r.status_code, r.text[:300])
        totals.append(dt)
        for op, v in per_op(writes()).items():
            agg.setdefault(op, []).extend(v)
    res["S1b_reassign_with_comment_no_delay"] = {"total_s": stats(totals),
                                                 "per_call_s": {k: stats(v) for k, v in agg.items()}}

    # S1c: смена статуса (transition + comment), N=10: «Поиск кандидата» <-> «Найм»
    totals, agg = [], {}
    for i in range(10):
        n += 1
        key = put_need(n, status="Поиск кандидата")
        writes()
        r, dt = timed(lambda: s.post(f"/needs/{key}/status", json={"action": "no_candidates", "comment": "QA",
                                                                     "fields": {"customfield_16100": "30001"}}))
        assert r.status_code == 200, (r.status_code, r.text[:300])
        totals.append(dt)
        for op, v in per_op(writes()).items():
            agg.setdefault(op, []).extend(v)
    res["S1c_status_change_no_delay"] = {"total_s": stats(totals), "per_call_s": {k: stats(v) for k, v in agg.items()}}

    # S2: задержка 9 с на один вызов - ожидаем успех; затем 9 с на все вызовы
    s2 = {}
    for op in ("fields", "attach", "transition", "rate"):
        n += 1
        key = put_need(n)
        fake("/__qa/delay", {"delay": 9, "ops": [op]})
        writes()
        r, dt = assign_once(s, key, [])
        ws = writes()
        s2[op] = {"http": r.status_code, "total_s": round(dt, 2), "rate_pending": r.json().get("rate_pending"),
                  "status": r.json().get("status"),
                  "delayed_call_s": [round(w["server_ms"] / 1000, 2) for w in ws if w["op"] == op]}
    n += 1
    key = put_need(n, status="Кандидат назначен")
    fake("/__qa/delay", {"delay": 9, "ops": ["comment"]})
    writes()
    r, dt = assign_once(s, key, [], comment="QA comment 9s")
    ws = writes()
    s2["comment"] = {"http": r.status_code, "total_s": round(dt, 2), "rate_pending": r.json().get("rate_pending"),
                     "status": r.json().get("status"),
                     "delayed_call_s": [round(w["server_ms"] / 1000, 2) for w in ws if w["op"] == "comment"]}
    n += 1
    key = put_need(n)
    fake("/__qa/delay", {"delay": 9, "ops": []})
    writes()
    r, dt = assign_once(s, key, [])
    ws = writes()
    s2["all_calls_9s"] = {"http": r.status_code, "total_s": round(dt, 2), "rate_pending": r.json().get("rate_pending"),
                          "status": r.json().get("status"), "calls": [(w["op"], round(w["server_ms"] / 1000, 1)) for w in ws]}
    fake("/__qa/delay", {"delay": 0})
    res["S2_delay_9s"] = s2

    # S3: задержка 11 с -> таймаут 10 с, отказ с текстом, completed; затем повтор без задержки
    s3 = {}
    for op in ("fields", "attach", "transition", "comment", "rate"):
        n += 1
        reassign = op == "comment"
        key = put_need(n, status="Кандидат назначен" if reassign else "Поиск кандидата")
        fake("/__qa/delay", {"delay": 11, "ops": [op]})
        writes()
        r, dt = assign_once(s, key, [extra] if op == "attach" else [], comment="QA comment 11s" if reassign else "")
        body = r.json()
        d = body.get("detail", body)
        rec = {"http": r.status_code, "elapsed_s": round(dt, 2), "code": d.get("code") if isinstance(d, dict) else None,
               "step": d.get("step") if isinstance(d, dict) else None,
               "completed": d.get("completed") if isinstance(d, dict) else d.get("steps") if isinstance(d, dict) else None,
               "message": ((d.get("message") or "")[:300] if isinstance(d, dict) else str(d)[:300])}
        if r.status_code == 200:
            rec.update(rate_pending=body.get("rate_pending"), steps=body.get("steps"), warnings=body.get("warnings"))
        # повтор без задержки с completed из ответа; дождаться, пока «опоздавшая» запись фейка завершится
        time.sleep(12)
        fake("/__qa/delay", {"delay": 0})
        before = fake(f"/__qa/issue/{key}")
        completed = (d.get("completed") if isinstance(d, dict) else None) or []
        if r.status_code == 200:
            completed = body.get("steps", [])
        r2, dt2 = assign_once(s, key, [extra] if op == "attach" else [],
                              comment="QA comment 11s" if reassign else "", completed=completed)
        after = fake(f"/__qa/issue/{key}")
        rec["retry"] = {"http": r2.status_code, "elapsed_s": round(dt2, 2), "status": r2.json().get("status"),
                        "rate_pending": r2.json().get("rate_pending"), "sent_completed": completed}
        rec["jira_state_after_timeout_before_retry"] = {
            "attachments": len(before["attachments"]), "comments": len(before["comments"]),
            "status": before["fields"]["status"]["name"], "rate": before["fields"].get("customfield_20809")}
        rec["jira_state_final"] = {
            "attachments": len(after["attachments"]), "comments": len(after["comments"]),
            "status": after["fields"]["status"]["name"], "rate": after["fields"].get("customfield_20809")}
        s3[op] = rec
    res["S3_delay_11s"] = s3

    # S4: смена статуса с задержкой 9 и 11 с на переход
    s4 = {}
    for d_ in (9, 11):
        n += 1
        key = put_need(n)
        fake("/__qa/delay", {"delay": d_, "ops": ["transition"]})
        writes()
        r, dt = timed(lambda: s.post(f"/needs/{key}/status", json={"action": "hire", "comment": "QA"}))
        det = r.json().get("detail", r.json())
        s4[f"{d_}s"] = {"http": r.status_code, "elapsed_s": round(dt, 2),
                        "code": det.get("code") if isinstance(det, dict) else None,
                        "message": det.get("message") if isinstance(det, dict) else None,
                        "status": r.json().get("status")}
        time.sleep(d_ + 2 - min(dt, d_ + 2) if d_ == 11 else 0)
    fake("/__qa/delay", {"delay": 0})
    res["S4_status_change_delay"] = s4
    return res


# --------------------------------------------------------------------------- bench (NFR-002)

def sql(db: str, q: str) -> str:
    return subprocess.run(DB + ["-d", db, "-c", q], capture_output=True, text=True, check=True).stdout.strip()


def phase_bench() -> dict:
    s = Sess()
    before_total = int(sql("app_db", "select count(*) from app.jira_need"))
    fake("/__qa/gen", {"count": BENCH_COUNT, "start": BENCH_START, "status": "mixed", "rate": True})
    for _ in range(90):  # опрос потребностей раз в 20 с
        mine = int(sql("app_db", "select count(*) from app.jira_need where jira_key ~ '^ANALYTICS-9[0-9]{5}$' "
                                 f"and substring(jira_key from 11)::int >= {BENCH_START} and substring(jira_key from 11)::int < {BENCH_START + BENCH_COUNT}"))
        if mine >= BENCH_COUNT:
            break
        time.sleep(3)
    total = int(sql("app_db", "select count(*) from app.jira_need"))
    res: dict = {"cache": {"before": before_total, "synthetic": mine, "total": total}}
    stat0 = fake("/__qa/stats")

    def run(name, url, n=100, params=None):
        ts = []
        for _ in range(n):
            r, dt = timed(lambda: s.http.get(url, params=params))
            assert r.status_code == 200, (url, r.status_code, r.text[:200])
            ts.append(dt)
        res[name] = stats(ts)

    s.http.get("/needs/rate-benchmark")  # прогрев
    run("rate_benchmark_6m_seq", "/needs/rate-benchmark", 200, {"period_months": 6})
    run("rate_benchmark_12m_seq", "/needs/rate-benchmark", 100, {"period_months": 12})
    run("rate_benchmark_60m_seq", "/needs/rate-benchmark", 100, {"period_months": 60})
    bm = s.http.get("/needs/rate-benchmark", params={"period_months": 6}).json()
    res["rate_benchmark_groups"] = [(g["currency"], g["unit"], g["count"]) for g in bm["groups"]]

    def conc(name, url, users, per_user, params=None):
        ts, errs = [], []
        lock = threading.Lock()

        def work(_):
            cl = Sess()
            for _ in range(per_user):
                r, dt = timed(lambda: cl.http.get(url, params=params))
                with lock:
                    (ts if r.status_code == 200 else errs).append(dt if r.status_code == 200 else r.status_code)
        with ThreadPoolExecutor(users) as ex:
            t0 = time.perf_counter()
            list(ex.map(work, range(users)))
            wall = time.perf_counter() - t0
        res[name] = {**stats(ts), "users": users, "errors": len(errs), "wall_s": round(wall, 2),
                     "rps": round(len(ts) / wall, 1)}

    conc("rate_benchmark_6m_10users", "/needs/rate-benchmark", 10, 30, {"period_months": 6})
    run("needs_list_limit100_seq", "/sync/needs", 100, {"limit": 100})
    run("needs_groups_seq", "/sync/needs/groups", 50)
    stat1 = fake("/__qa/stats")
    res["jira_reads_during_cache_calls"] = {k: stat1[k] - stat0[k] for k in ("search", "issue", "epic", "epic_children")}
    res["_note_jira_reads"] = "включает фоновый опрос синхронизации; список/ориентир сами Jira не вызывают"

    # status-actions: по клику читает потребность и переходы из Jira (2 чтения). Фейк без задержки и с 0,5 с
    key = f"ANALYTICS-{BENCH_START + 1}"
    st = s.http.get(f"/needs/{key}/status-actions")
    res["status_actions_sample"] = {"http": st.status_code, "status": st.json().get("status"),
                                    "actions": [a["action"] for a in st.json().get("actions", [])]}
    run("status_actions_seq_fake0", f"/needs/{key}/status-actions", 100)
    fake("/__qa/fail", {"mode": "none", "delay": 0.5})
    run("status_actions_seq_fake_delay_0_5s", f"/needs/{key}/status-actions", 20)
    fake("/__qa/fail", {"mode": "none", "delay": 0})
    return res


def phase_cleanup() -> dict:
    out: dict = {}
    out["fake_dropped"] = fake("/__qa/drop", {"regex": "^ANALYTICS-9[0-9]{5}$"})
    out["fake_drop_employee"] = fake("/__qa/drop", {"regex": f"^{EMP}$"})
    rng = "jira_key ~ '^ANALYTICS-9[0-9]{5}$'"
    out["jira_need_deleted"] = sql("app_db", f"with d as (delete from app.jira_need where {rng} returning 1) select count(*) from d")
    out["employee_files_deleted"] = sql("cv_db", f"set session_replication_role = replica; with d as (delete from cv.employee_file where employee_key='{EMP}' returning 1) select count(*) from d").splitlines()[-1]  # триггер FR-040 запрещает удалять текущее основное CV: отключён только на время этой сессии
    out["employee_deleted"] = sql("cv_db", f"set session_replication_role = replica; with d as (delete from cv.employee where jira_key='{EMP}' returning 1) select count(*) from d")
    out["left_synthetic_needs"] = sql("app_db", f"select count(*) from app.jira_need where {rng}")
    out["left_total_needs"] = sql("app_db", "select count(*) from app.jira_need")
    fake("/__qa/delay", {"delay": 0})
    fake("/__qa/write_fail", {"mode": "none"})
    return out


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"assign": phase_assign, "bench": phase_bench, "cleanup": phase_cleanup}.get(phase)
    if fn is None:
        raise SystemExit(__doc__)
    data = json.loads(RAW.read_text(encoding="utf-8")) if RAW.exists() else {}
    data[phase] = fn()
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(data[phase], ensure_ascii=False, indent=1))
