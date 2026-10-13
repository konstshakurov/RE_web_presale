"""Нагрузка NFR-002 / NFR-008 (KON-47): 4 пользователя, смесь «список по группе : поиск : сортировка».

Запуск (стенд поднят, кэш подготовлен ``python tests_load/run_load.py``):
    locust -f tests_load/locustfile.py --headless -u 4 -r 4 -t 60s --host http://127.0.0.1:18080
Пароли берутся из ``qa/stand/.qa.env`` (вне Git) или из окружения; в вывод не попадают.
Порог: p95 каждого запроса <= 2000 мс (NFR-002/008), доля отказов 0.
"""
import itertools
import os
from pathlib import Path

from locust import HttpUser, between, events, task

ENV = {}
_f = Path(__file__).resolve().parents[1] / "qa" / "stand" / ".qa.env"
if _f.exists():
    for line in _f.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            ENV[k.strip()] = v.strip()
ENV.update({k: v for k, v in os.environ.items() if k.startswith("QA_")})

LOGINS = [("qa-admin", "QA_LDAP_PW_ADMIN"), ("qa-user", "QA_LDAP_PW_USER"),
          ("qa-revoked", "QA_LDAP_PW_REVOKED"), ("qa-jirablock", "QA_LDAP_PW_JIRABLOCK")]
_cycle = itertools.cycle(LOGINS)
P95_LIMIT_MS = 2000


class Analyst(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        login, key = next(_cycle)
        r = self.client.post("/api/auth/login", json={"login": login, "password": ENV[key]}, name="login")
        if r.status_code != 200:
            raise RuntimeError(f"вход {login} не удался: {r.status_code}")
        groups = self.client.get("/api/sync/needs/groups", name="groups").json()["groups"]
        self.groups = [g["key"] for g in groups] or ["all"]
        self._i = 0

    @task(5)
    def needs_by_group(self):
        g = self.groups[self._i % len(self.groups)]
        self._i += 1
        self.client.get(f"/api/sync/needs?group={g}&limit=100", name="needs?group")

    @task(2)
    def needs_search(self):
        self.client.get("/api/sync/needs?q=ANALYTICS-10&limit=100", name="needs?q")

    @task(1)
    def needs_sort_by_date(self):
        self.client.get("/api/sync/needs?sort=updated&order=desc&limit=100", name="needs?sort=updated")

    @task(1)
    def employees(self):
        self.client.get("/api/sync/employees?limit=100", name="employees")

    @task(1)
    def resume(self):
        self.client.get("/api/resume/employees?limit=100", name="resume")


@events.quitting.add_listener
def _check_thresholds(environment, **kw):
    bad = []
    for name, st in environment.stats.entries.items():
        p95 = st.get_response_time_percentile(0.95)
        if name[0] != "login" and p95 and p95 > P95_LIMIT_MS:
            bad.append(f"{name[0]}: p95={p95:.0f} мс")
    if environment.stats.total.fail_ratio > 0:
        bad.append(f"доля отказов {environment.stats.total.fail_ratio:.1%}")
    if bad:
        print("ПОРОГ NFR-002/008 НЕ ВЫПОЛНЕН: " + "; ".join(bad))
        environment.process_exit_code = 1
