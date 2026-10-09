"""QA-4 (KON-56): qa/nfr006_check.py проверяется на грязных и чистых данных (NFR-006)."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "qa" / "nfr006_check.py"
spec = importlib.util.spec_from_file_location("nfr006_check", SCRIPT)
nfr = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = nfr
spec.loader.exec_module(nfr)

CANARY = "FAKE_canary_pw_77"


def test_selftest_passes_and_reports_violations_on_dirty_data(capsys):
    assert nfr.selftest() == 0
    assert "SELFTEST: пройден" in capsys.readouterr().out


@pytest.mark.parametrize("line", [
    '{"password": "FAKE_plain_pw_1"}',
    "password=FAKE_plain_pw_1",
    "Authorization: Bearer FAKEBEARER000111",
    "Set-Cookie: JSESSIONID=FAKESESSION000111; Path=/",
    "dsn postgresql://app:FAKEdbpass99@db/app_db",
])
def test_logs_dirty_lines_are_flagged(line):
    assert nfr.scan_text(line, "t"), line


@pytest.mark.parametrize("line", [
    '{"password": "***"}', "password=***", "Authorization: ***", "Set-Cookie: ***",
    "dsn postgresql://app:***@db/app_db", '{"msg": "вход выполнен: ivan"}',
])
def test_logs_redacted_lines_are_clean(line):
    assert nfr.scan_text(line, "t") == []


def test_finding_never_prints_secret_value():
    f = nfr.scan_text("password=FAKE_plain_pw_123456", "t")[0]
    assert "FAKE_plain_pw_123456" not in str(f)


def test_canary_from_env_is_found_in_logs(monkeypatch):
    monkeypatch.setenv("NFR006_CANARIES", CANARY)
    assert nfr.check_logs_text(f"msg {CANARY}", "t")
    assert nfr.check_logs_text("msg ok", "t") == []


def test_secret_store_real_ciphertext_ok_plaintext_flagged():
    good = {"id": 1, "kind": "jira_session", "ciphertext": os.urandom(48),
            "nonce": os.urandom(12), "key_id": "k1"}
    assert nfr.check_secret_rows([good]) == []
    plain = dict(good, ciphertext=b"FAKE_open_token_for_probe_0001")
    assert nfr.check_secret_rows([plain], ["FAKE_open_token"])
    assert nfr.check_secret_rows([dict(good, nonce=b"123")])
    assert nfr.check_secret_rows([dict(good, key_id="")])


def test_db_cells_flag_token_like_and_canary(monkeypatch):
    assert nfr.check_db_cells([("app.t.c", "A" * 40)])
    assert nfr.check_db_cells([("app.t.c", f"x{CANARY}")], [CANARY])
    assert nfr.check_db_cells([("app.app_user.ldap_login", "ivan")]) == []


def test_repo_scan_flags_key_and_env_file_and_ignores_env_reads(tmp_path):
    (tmp_path / "a.py").write_text('APP_ENC_KEY = "' + "QUJD" * 10 + 'ABC="\n', encoding="utf-8")
    (tmp_path / "b.py").write_text('key = os.environ["APP_ENC_KEY"]\n', encoding="utf-8")
    (tmp_path / "k.pem.txt").write_text("-----BEGIN " + "PRIVATE KEY-----\n", encoding="utf-8")
    found = nfr.scan_repo_tree(tmp_path)
    paths = {f.where for f in found}
    assert "a.py" in paths and "k.pem.txt" in paths and "b.py" not in paths
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    assert ".env" in {f.where for f in nfr.scan_repo_tree(tmp_path)}


def test_real_repo_has_no_findings():
    """Сам репозиторий (отслеживаемые и неигнорируемые файлы) на момент проверки чист."""
    findings = [f for f in nfr.run_repo(nfr.ROOT)]
    # Известное замечание QA-4: тестовая константа без префикса FAKE_ (см. отчёт); остальное чисто.
    unexpected = [f for f in findings if "tests/unit/test_auth.py" not in f.where]
    assert unexpected == [], [str(f) for f in unexpected]
