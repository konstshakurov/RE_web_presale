"""Подготовка кэша 500/2000 потребностей на QA-стенде, прогон locust, очистка (KON-47, NFR-002/008).

    python tests_load/run_load.py [--needs 500 2000] [--duration 60]
Использует фейк Jira (``/__qa/gen``) и ``POST /sync/refresh`` пользователем qa-user; по окончании
удаляет нагрузочные записи (ключи ANALYTICS-2xxxx) из кэша и сбрасывает фейк Jira.
"""
import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tests.qa._fakes import JiraCtl  # noqa: E402
from tests.qa._stage1 import cleanup_generated_needs, ensure_allow_list, prepare_cache  # noqa: E402
from tests.qa._stand import BASE_URL, logged_in  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--needs", type=int, nargs="+", default=[500, 2000])
    ap.add_argument("--duration", type=int, default=60)
    a = ap.parse_args()
    ensure_allow_list()
    jira, s = JiraCtl(), logged_in("qa-user")
    prepare_cache(s, jira)
    code = 0
    try:
        generated = 0
        for target in a.needs:
            jira.gen(target - generated, start=20000 + generated, status="mixed")
            generated = target
            assert s.post("/sync/refresh", json={"scope": "needs"}).status_code == 200
            total = s.http.get("/sync/needs", params={"limit": 1}).json()["total"]
            out = ROOT / "tests_load" / f"result_{target}"
            cmd = [sys.executable, "-m", "locust", "-f", str(ROOT / "tests_load" / "locustfile.py"), "--headless",
                   "-u", "4", "-r", "4", "-t", f"{a.duration}s", "--host", BASE_URL, "--csv", str(out),
                   "--only-summary"]
            print(f"== в кэше {total} потребностей, нагрузка {a.duration} с ==", flush=True)
            r = subprocess.run(cmd)
            code = code or r.returncode
            stats = out.with_name(out.name + "_stats.csv")
            if stats.exists():
                for row in csv.DictReader(stats.open(encoding="utf-8")):
                    print(f"{row['Name']:<24} n={row['Request Count']:>5} fail={row['Failure Count']:>3} "
                          f"p50={row['50%']:>5} p95={row['95%']:>5} max={row['Max Response Time'][:6]:>6}")
    finally:
        cleanup_generated_needs()
        jira.reset()
        time.sleep(1)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
