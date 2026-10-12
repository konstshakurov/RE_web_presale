"""QA-5 (KON-57): приёмка NFR-006 (четыре критерия) и NFR-007 п.3 на живом стенде.

Запускается после ``test_stand_api_qa.py`` (имя файла сортируется позже): к этому моменту через
стенд прошли входы, отказы и сессии Jira, то есть в логах и БД есть что искать. Канарейки -
тестовые пароли стенда, токен фейк Jira (префикс ``fake-session-``) и ключи шифрования стенда.
Значения в отчёты не попадают: скрипт ``qa/nfr006_check.py`` печатает только маску.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ._stand import CFG, COMPOSE_PROJECT, PASSWORDS, ROOT, Session, logged_in, requires_stand

pytestmark = requires_stand

SCRIPT = ROOT / "qa" / "nfr006_check.py"
DB_PORT = CFG.get("QA_DB_PORT", "15432")


def _canaries() -> list[str]:
    values = list(PASSWORDS.values()) + [
        CFG.get("QA_JIRA_RELOGIN_PW", ""), "FAKE_wrong_password_1", "FAKE_wrong_jira_pw",
        "fake-session-", CFG.get("APP_ENC_KEY", ""), CFG.get("JIRA_TOKEN_ENCRYPTION_KEY", ""),
    ]
    return [v for v in values if len(v) >= 4]


def _run(args: list[str], canaries: list[str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "NFR006_CANARIES": ",".join(canaries if canaries is not None else _canaries()),
           "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=300)


def _db_url(db: str = "app_db", user: str = "app_rw", pw_key: str = "APP_RW_PASSWORD") -> str:
    return f"postgresql://{user}:{CFG[pw_key]}@127.0.0.1:{DB_PORT}/{db}"


@pytest.fixture(scope="module", autouse=True)
def traffic():
    """Свежий трафик перед проверкой: успех, отказ, отказ Jira, повторный ввод пароля Jira."""
    ok = logged_in("qa-user")
    Session().login("qa-user", "FAKE_wrong_password_1")
    blocked = Session()
    blocked.login("qa-jirablock")
    blocked.post("/auth/jira-relogin", json={"password": "FAKE_wrong_jira_pw"})
    blocked.post("/auth/jira-relogin", json={"password": CFG["QA_JIRA_RELOGIN_PW"]})
    ok.post("/auth/logout")
    yield


def _compose_logs(tmp_path: Path) -> Path:
    names = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={COMPOSE_PROJECT}",
         "--format", "{{.Names}}"], capture_output=True, text=True, timeout=60).stdout.split()
    assert names, "контейнеры QA-стенда не найдены"
    out = tmp_path / "stand.log"
    with out.open("w", encoding="utf-8") as f:
        for n in names:
            r = subprocess.run(["docker", "logs", n], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
            f.write(r.stdout + r.stderr)
    return out


def test_nfr006_c1_no_open_token_in_secret_store():
    """Критерий 1: в БД нет открытых токенов (шифртекст, nonce 12 байт, key_id)."""
    import psycopg

    with psycopg.connect(_db_url(), connect_timeout=5) as c:
        n = c.execute("SELECT count(*) FROM app.secret_store").fetchone()[0]
    assert n >= 1, "в secret_store нет записей: проверка была бы пустой (нет входа в Jira?)"
    r = _run(["--db-url", _db_url()])
    assert r.returncode == 0, r.stdout[-1500:]
    assert "нарушений нет" in r.stdout


def test_nfr006_c2_key_not_in_repo_or_git():
    """Критерий 2: ключ шифрования не в коде и не в репозитории (скрипт + git grep значений стенда)."""
    r = _run(["--repo"])
    assert r.returncode == 0, r.stdout[-1500:]
    for key in ("APP_ENC_KEY", "JIRA_TOKEN_ENCRYPTION_KEY"):
        value = CFG[key]
        g = subprocess.run(["git", "grep", "-I", "-l", "-F", "-e", value], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        assert g.returncode == 1 and g.stdout == "", f"значение {key} найдено в файлах репозитория"
    tracked = subprocess.run(["git", "ls-files", "--", "*.env", ".env", "qa/stand/.qa.env"], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
    assert tracked == [], f".env в индексе Git: {tracked}"


def test_nfr006_c3_password_not_stored_anywhere(tmp_path):
    """Критерий 3: пароль нигде не сохранён: ни в текстовых колонках, ни в дампе БД."""
    r = _run(["--db-url", _db_url()])
    assert r.returncode == 0, r.stdout[-1500:]
    dump = subprocess.run(
        ["docker", "exec", f"{COMPOSE_PROJECT}-db-1", "pg_dump", "-U", "postgres", "-d", "app_db", "--inserts"],
        capture_output=True, timeout=120)
    assert dump.returncode == 0
    body = dump.stdout
    for canary in _canaries():
        raw = canary.encode()
        assert raw not in body, "канарейка найдена в дампе app_db (текст)"
        assert raw.hex().encode() not in body.lower(), "канарейка найдена в дампе app_db (bytea)"


def test_nfr006_c4_logs_without_secrets(tmp_path):
    """Критерий 4: логи (api, worker, proxy, db, ldap, fake-jira) без паролей и токенов."""
    log = _compose_logs(tmp_path)
    assert log.stat().st_size > 2000
    r = _run(["--logs-file", str(log)])
    assert r.returncode == 0, r.stdout[-1500:]
    text = log.read_text(encoding="utf-8", errors="replace")
    for canary in _canaries():
        assert canary not in text, "канарейка в логах стенда"


def test_nfr006_probe_script_detects_planted_leak(tmp_path):
    """Контроль самой проверки: подброшенный в лог пароль обязан быть найден (код 1)."""
    log = _compose_logs(tmp_path)
    with log.open("a", encoding="utf-8") as f:
        f.write(f'\n{{"msg":"probe","password":"{PASSWORDS["qa-user"]}"}}\n')
    r = _run(["--logs-file", str(log)])
    assert r.returncode == 1
    assert PASSWORDS["qa-user"] not in r.stdout           # даже находка не печатает значение


def test_nfr007_p3_app_rw_cannot_reach_cv_db_and_back():
    """NFR-007 п.3 (выборочно): app_rw не видит cv_db, cv_rw не видит app_db."""
    import psycopg

    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(_db_url("cv_db", "app_rw", "APP_RW_PASSWORD"), connect_timeout=5)
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(_db_url("app_db", "cv_rw", "CV_RW_PASSWORD"), connect_timeout=5)
    with psycopg.connect(_db_url("cv_db", "cv_rw", "CV_RW_PASSWORD"), connect_timeout=5) as c:
        assert c.execute("SELECT 1").fetchone()[0] == 1       # свой доступ жив
    with psycopg.connect(_db_url(), connect_timeout=5) as c:
        # в app_db нет таблиц CV-данных сотрудников
        names = {r[0] for r in c.execute(
            "SELECT table_schema||'.'||table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog','information_schema')").fetchall()}
        assert not any("employee_file" in n or "cv_" in n for n in names), names
