#!/usr/bin/env python3
"""Проверка NFR-006 на локальном стенде (KON-56): секреты не хранятся и не светятся открыто.

Четыре проверки (04_nfr_matrix.md, NFR-006):
  secret_store  выборка app.secret_store: в ciphertext нет открытого токена, nonce 12 байт, key_id задан;
  logs          в логах (контейнеры compose или файл) нет паролей, токенов, Authorization, JSESSIONID;
  db            в текстовых колонках схемы app нет «канареек» и токеноподобных значений;
  repo          в репозитории нет ключа шифрования, закрытых ключей, значений секретов в .env.

«Канарейки» - заведомо известные значения (тестовый пароль, тестовый токен), которые прогнали через стенд;
передаются через окружение NFR006_CANARIES (через запятую), в отчёт не попадают: только маска.

Запуск (из корня репозитория):
  python qa/nfr006_check.py --selftest                 # негативная проба: на «грязных» данных ДОЛЖНО найти нарушения
  python qa/nfr006_check.py --repo                     # только репозиторий
  python qa/nfr006_check.py --logs-compose             # docker compose logs
  python qa/nfr006_check.py --logs-file path.log
  python qa/nfr006_check.py --db-url "$APP_DB_URL"     # secret_store + текстовые колонки (нужен psycopg)
Код возврата: 0 - нарушений нет (или selftest увидел нарушения), 1 - найдены нарушения, 2 - ошибка запуска.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Finding:
    check: str
    where: str
    what: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.where}: {self.what}"


def mask(value: str) -> str:
    """Не печатаем секрет целиком: длина и первые 2 символа."""
    return f"<{len(value)} симв., {value[:2]}...>"


def canaries_from_env() -> list[str]:
    return [c for c in os.environ.get("NFR006_CANARIES", "").split(",") if len(c) >= 4]


# --- logs ---------------------------------------------------------------------------------------

_REDACTED = {"***", "", "null", "none", "[redacted]", "<redacted>"}
_LOG_PATTERNS = (
    ("пароль/токен/ключ в значении",
     re.compile(r"""(?ix)["']?[\w-]*(?:password|passwd|pwd|token|secret|api[_-]?key|enc[_-]?key)[\w-]*["']?\s*[:=]\s*
                     (?P<v>"[^"]*"|'[^']*'|[^\s,;&}'"]+)""")),
    ("Authorization/Bearer", re.compile(r"(?i)\b(?:bearer|basic)\s+(?P<v>[A-Za-z0-9._~+/=-]{8,})")),
    ("Cookie/Set-Cookie/JSESSIONID",
     re.compile(r"""(?ix)(?:jsessionid|set-cookie|\bcookie|re_session)["']?\s*[:=]\s*(?P<v>[^\s;,"']{8,})""")),
    ("userinfo в URL", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]+:(?P<v>[^\s@/]+)@")),
)


def _is_redacted(v: str) -> bool:
    v = v.strip().strip("\"'")
    return v.lower() in _REDACTED or set(v) <= {"*"}


def scan_text(text: str, where: str, canaries: list[str] = ()) -> list[Finding]:
    """Найти в тексте открытые секреты. Построчно, чтобы указать номер строки."""
    out: list[Finding] = []
    for no, line in enumerate(text.splitlines(), 1):
        for c in canaries:
            if c in line:
                out.append(Finding("logs", f"{where}:{no}", f"канарейка {mask(c)} в открытом виде"))
        for label, rx in _LOG_PATTERNS:
            for m in rx.finditer(line):
                if not _is_redacted(m.group("v")):
                    out.append(Finding("logs", f"{where}:{no}", f"{label}: значение {mask(m.group('v'))}"))
    return out


def check_logs_text(text: str, where: str = "log") -> list[Finding]:
    return scan_text(text, where, canaries_from_env())


# --- secret_store / db --------------------------------------------------------------------------

def _entropy(b: bytes) -> float:
    if not b:
        return 0.0
    n = len(b)
    return -sum(c / n * math.log2(c / n) for c in Counter(b).values())


def check_secret_rows(rows: list[dict], canaries: list[str] = ()) -> list[Finding]:
    """rows: id, kind, ciphertext(bytes), nonce(bytes), key_id."""
    out: list[Finding] = []
    for r in rows:
        where = f"secret_store#{r.get('id')}({r.get('kind')})"
        ct, nonce = bytes(r["ciphertext"]), bytes(r["nonce"])
        if len(nonce) != 12:
            out.append(Finding("secret_store", where, f"nonce {len(nonce)} байт, ожидается 12"))
        if not r.get("key_id"):
            out.append(Finding("secret_store", where, "не задан key_id"))
        for c in canaries:
            if c.encode() in ct or c.encode() in nonce:
                out.append(Finding("secret_store", where, f"канарейка {mask(c)} в открытом виде"))
        try:
            text = ct.decode("utf-8")
            if text.isprintable():
                out.append(Finding("secret_store", where, "ciphertext читается как печатный текст"))
        except UnicodeDecodeError:
            pass
        body = ct[:-16] if len(ct) > 16 else ct
        if len(body) >= 24 and _entropy(body) < 4.5:
            out.append(Finding("secret_store", where, f"низкая энтропия ({_entropy(body):.1f} бит/байт): не похоже на шифртекст"))
        if len(ct) < 16:
            out.append(Finding("secret_store", where, "ciphertext короче тега GCM"))
    return out


_TOKENISH = re.compile(r"^(?:[A-Za-z0-9_-]{32,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*)$")


def check_db_cells(cells: list[tuple[str, str]], canaries: list[str] = ()) -> list[Finding]:
    """cells: (table.column, значение). Канарейки и токеноподобные строки в text-колонках - нарушение."""
    out: list[Finding] = []
    for where, val in cells:
        for c in canaries:
            if c in val:
                out.append(Finding("db", where, f"канарейка {mask(c)} в открытом виде"))
        if _TOKENISH.match(val.strip()):
            out.append(Finding("db", where, f"токеноподобное значение {mask(val)}"))
    return out


def run_db(url: str) -> list[Finding]:
    try:
        import psycopg
    except ImportError:
        print("psycopg не установлен: проверки db/secret_store пропущены", file=sys.stderr)
        return []
    canaries = canaries_from_env()
    findings: list[Finding] = []
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, kind, ciphertext, nonce, key_id FROM app.secret_store")
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        print(f"secret_store: строк {len(rows)}")
        findings += check_secret_rows(rows, canaries)
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema='app' AND data_type IN ('text','character varying','jsonb','json') "
            "AND NOT (table_name='secret_store')"
        )
        for table, col in cur.fetchall():
            cur.execute(f'SELECT "{col}"::text FROM app."{table}" WHERE "{col}" IS NOT NULL LIMIT 5000')
            findings += check_db_cells([(f"app.{table}.{col}", v[0]) for v in cur.fetchall()], canaries)
    return findings


# --- repo ---------------------------------------------------------------------------------------

_B64_32 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{43}=(?![A-Za-z0-9+/=])")
_ENV_SECRET = re.compile(
    r"""(?im)^\s*(?:export\s+)?([A-Z0-9_]*(?:ENC_KEY|ENCRYPTION_KEY|PASSWORD|SECRET|TOKEN|API_KEY)[A-Z0-9_]*)\s*[:=]\s*["']?([^\s"'#$<{][^\s"'#]*)"""
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----")
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".history"}
_BIN = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".docx", ".woff", ".woff2", ".zip", ".pyc", ".svg"}


def _is_placeholder(v: str) -> bool:
    return v.upper().startswith("FAKE_") or v.lower() in {"changeme", "example", "xxx", "true", "false"}


def _looks_like_secret_value(v: str) -> bool:
    """Литерал, похожий на настоящий секрет: >=16 символов, буквы и цифры, не плейсхолдер/идентификатор."""
    if len(v) < 16 or _is_placeholder(v) or v.startswith("__"):
        return False
    return any(ch.isdigit() for ch in v) and any(ch.isalpha() for ch in v) and not re.fullmatch(r"[A-Za-z_.()\[\]]+", v)


def scan_repo_tree(root: Path, extra_ignore: tuple[str, ...] = ("qa/nfr006_check.py",)) -> list[Finding]:
    out: list[Finding] = []
    for p in root.rglob("*"):
        if not p.is_file() or set(p.relative_to(root).parts) & _SKIP_DIRS or p.suffix.lower() in _BIN:
            continue
        rel = p.relative_to(root).as_posix()
        if rel in extra_ignore or rel.startswith(("output/", "input/", "estimate/", "skills/", "docs/")):
            continue
        if p.name == ".env":
            out.append(Finding("repo", rel, ".env внутри проверяемого дерева (должен быть вне Git)"))
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if _PRIVATE_KEY.search(text):
            out.append(Finding("repo", rel, "закрытый ключ"))
        for line in text.splitlines():
            if "FAKE_" in line or "os.environ" in line or "getenv" in line or "${" in line:
                continue
            for m in _ENV_SECRET.finditer(line):
                name, val = m.group(1), m.group(2)
                quoted_or_env = bool(re.search(r"[:=]\s*[\"']", line)) or p.suffix in {".env", ".ldif", ".yml", ".yaml", ".sh", ".example", ""}
                if quoted_or_env and _looks_like_secret_value(val):
                    out.append(Finding("repo", rel, f"{name} задан значением {mask(val)}"))
            if re.search(r"(?i)(ENC_KEY|ENCRYPTION_KEY)", line):
                for m in _B64_32.finditer(line):
                    if not _is_placeholder(m.group(0)):
                        out.append(Finding("repo", rel, f"base64 32 байта рядом с именем ключа {mask(m.group(0))}"))
    return out


def tracked_env_files(root: Path) -> list[Finding]:
    try:
        r = subprocess.run(["git", "ls-files", ".env", "*.pem", "*id_rsa*"], cwd=root,
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return [Finding("repo", f, "файл ключей/.env в индексе Git") for f in r.stdout.split()]


def run_repo(root: Path) -> list[Finding]:
    git_files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                               cwd=root, capture_output=True, text=True).stdout.split("\n")
    findings = tracked_env_files(root)
    # В Git-режиме смотрим только отслеживаемые и неигнорируемые файлы (локальный .env не считается).
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for f in filter(None, git_files):
            src = root / f
            if src.is_file():
                dst = t / f
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    dst.write_bytes(src.read_bytes())
                except OSError:
                    pass
        findings += scan_repo_tree(t)
    return findings


# --- logs: источники ----------------------------------------------------------------------------

def run_logs_compose() -> list[Finding]:
    r = subprocess.run(["docker", "compose", "logs", "--no-color", "--no-log-prefix"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0:
        print("docker compose logs не отработал: " + r.stderr.strip()[:200], file=sys.stderr)
        raise SystemExit(2)
    print(f"логи compose: {len(r.stdout.splitlines())} строк")
    return check_logs_text(r.stdout, "compose")


# --- selftest -----------------------------------------------------------------------------------

def selftest() -> int:
    """Негативная проба: на «грязных» данных каждая проверка обязана сообщить нарушение."""
    canary = "FAKE_canary_secret_9"
    dirty_log = (
        '{"msg":"login","password":"FAKE_plain_pw_123"}\n'
        "Authorization: Bearer FAKEBEARERTOKEN0001\n"
        "Set-Cookie: JSESSIONID=FAKESESSION0000001; Path=/\n"
        f"connect ldap://svc:FAKEurlpass99@host/ {canary}\n"
    )
    clean_log = '{"msg":"login","password":"***"}\nAuthorization: ***\nSet-Cookie: ***\n'
    dirty_rows = [
        {"id": 1, "kind": "jira_session", "ciphertext": b"FAKE_open_jira_token_00000000000", "nonce": b"123456789012", "key_id": "k1"},
        {"id": 2, "kind": "jira_session", "ciphertext": os.urandom(40), "nonce": b"short", "key_id": ""},
    ]
    clean_rows = [{"id": 3, "kind": "llm_key", "ciphertext": os.urandom(48), "nonce": os.urandom(12), "key_id": "k1"}]
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "settings.py").write_text('APP_ENC_KEY = "' + "QUJD" * 10 + 'ABC="\n', encoding="utf-8")
        (d / "deploy.env").write_text("JIRA_TOKEN_ENCRYPTION_KEY=" + "Zm9v" * 8 + "\nLDAP_BIND_PASSWORD=FAKE_ok\n", encoding="utf-8")
        (d / "id.txt").write_text("-----BEGIN PRIVATE KEY-----\nxx\n", encoding="utf-8")
        (d / ".env").write_text("A=1\n", encoding="utf-8")
        repo_dirty = scan_repo_tree(d)
        (d / "settings.py").write_text('APP_ENC_KEY = os.environ["APP_ENC_KEY"]\n', encoding="utf-8")
        (d / "deploy.env").write_text("LDAP_BIND_PASSWORD=\n", encoding="utf-8")
        (d / "id.txt").unlink()
        (d / ".env").unlink()
        repo_clean = scan_repo_tree(d)

    cases = [
        ("logs dirty", scan_text(dirty_log, "probe", [canary]), 5),
        ("logs clean", scan_text(clean_log, "probe", [canary]), 0),
        ("secret_store dirty", check_secret_rows(dirty_rows, [canary, "FAKE_open_jira_token"]), 3),
        ("secret_store clean", check_secret_rows(clean_rows, [canary]), 0),
        ("db dirty", check_db_cells([("app.t.c", canary + "x"), ("app.t.d", "A" * 40)], [canary]), 2),
        ("db clean", check_db_cells([("app.t.c", "обычный текст"), ("app.t.d", "boss")], [canary]), 0),
        ("repo dirty", repo_dirty, 4),
        ("repo clean", repo_clean, 0),
    ]
    bad = 0
    for name, found, minimum in cases:
        ok = (len(found) >= minimum) if minimum else (len(found) == 0)
        print(f"{'OK  ' if ok else 'FAIL'} {name}: найдено {len(found)}, ожидалось {'>=' + str(minimum) if minimum else '0'}")
        bad += not ok
    for f in cases[0][1][:3]:
        print("   пример:", f)
    print("SELFTEST:", "пройден (нарушения на грязных данных обнаружены)" if not bad else "ПРОВАЛЕН")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--repo", action="store_true")
    ap.add_argument("--logs-compose", action="store_true")
    ap.add_argument("--logs-file")
    ap.add_argument("--db-url", help="DSN БД приложения (передавайте через переменную окружения, не в историю)")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.repo or a.logs_compose or a.logs_file or a.db_url):
        ap.print_help()
        return 2
    findings: list[Finding] = []
    if a.repo:
        findings += run_repo(ROOT)
    if a.logs_compose:
        findings += run_logs_compose()
    if a.logs_file:
        findings += check_logs_text(Path(a.logs_file).read_text(encoding="utf-8", errors="replace"), a.logs_file)
    if a.db_url:
        findings += run_db(a.db_url)
    for f in findings:
        print("НАРУШЕНИЕ", f)
    print(f"ИТОГ NFR-006: {'нарушений нет' if not findings else str(len(findings)) + ' наруш.'}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
