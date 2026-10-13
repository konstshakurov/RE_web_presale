"""KON-47 (Pre.QA-2), Q47-03/06/07/13: шлюз LLM на стенде - настройка провайдеров, обезличивание «на
проводе», подтверждение, таймаут и повтор, секреты.

Требования: FR-023, FR-030, NFR-010, NFR-006 (ключ внешнего провайдера, промпты задач, логи).
Фейк LLM (``qa/stand/fake_llm.py``) журналирует тела запросов: «на проводе» - это ровно то, что принял
провайдер. «Внешний» провайдер: HTTPS-адрес для внешнего API обязателен (проверка ниже), поэтому для
сквозного прогона провайдер создаётся через API как внутренний (http, ключ сохраняется приложением), а
признак ``is_external`` выставляется SQL-запросом в БД стенда - приложение дальше работает как с внешним.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from ._fakes import EXT_KEY, LlmCtl, db, requires_fakes, wait_until
from ._stage1 import cleanup_llm, ensure_allow_list
from ._stand import CFG, COMPOSE_PROJECT, ROOT, Session, logged_in, requires_stand

pytestmark = [requires_stand, requires_fakes]

FAKE_LLM_INTERNAL = "http://fake-llm:8080/v1"
PROVIDERS = ("QA-ext", "QA-slow", "QA-https")
MARKER = "QA47MARK" + uuid.uuid4().hex[:10]          # уникальная строка в промптах: поиск в БД и логах
FINISHED = {"done", "failed", "timeout", "cancelled"}
DATASET = json.loads((ROOT / "qa" / "testdata" / "fr023_anonymization_set.json").read_text(encoding="utf-8"))["examples"]


@pytest.fixture(scope="module")
def admin():
    a = ensure_allow_list()
    cleanup_llm(PROVIDERS)
    yield a
    cleanup_llm(PROVIDERS)
    a.close()


@pytest.fixture(scope="module")
def user(admin):
    s = logged_in("qa-user")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def llm():
    c = LlmCtl()
    c.reset()
    yield c
    c.reset()


@pytest.fixture(scope="module")
def ext_provider(admin) -> dict:
    """«Внешний» провайдер: создан API (ключ шифруется приложением), признак внешнего - SQL (см. docstring)."""
    r = admin.post("/admin/llm/providers", json={
        "name": "QA-ext", "url": FAKE_LLM_INTERNAL, "is_external": False, "api_key": EXT_KEY,
        "timeout_sec": 30, "model": "fake-ext-model"})
    assert r.status_code == 201, r.text
    p = r.json()
    with db("app_db") as c:
        c.execute("UPDATE app.llm_provider SET is_external = true WHERE id = %s", (p["id"],))
    return p


@pytest.fixture(scope="module")
def slow_provider(admin) -> dict:
    r = admin.post("/admin/llm/providers", json={
        "name": "QA-slow", "url": FAKE_LLM_INTERNAL, "is_external": False, "timeout_sec": 5,
        "model": "fake-slow-model"})
    assert r.status_code == 201, r.text
    return r.json()


def submit(s: Session, prompt: str, **kw):
    body = {"purpose": "qa_wire", "prompt": prompt, **kw}
    return s.post("/llm/tasks", json=body)


def wait_task(s: Session, task_id: str, timeout: float = 60) -> dict:
    def _done():
        t = s.http.get(f"/llm/tasks/{task_id}").json()
        return t if t["status"] in FINISHED else None
    return wait_until(_done, timeout=timeout, interval=1, what=f"задача {task_id[:8]} завершена")


def wire(llm: LlmCtl) -> list[dict]:
    return [c for c in llm.captured() if c["kind"] == "chat"]


# --- FR-030: настройка провайдеров -----------------------------------------------------------------

def test_fr030_default_provider_is_internal_and_visible_to_users(user):
    pubs = user.http.get("/llm/providers").json()
    d = [p for p in pubs if p["is_default"]]
    assert len(d) == 1 and d[0]["is_external"] is False
    assert set(d[0]) == {"id", "name", "is_external", "is_default"}      # без url и сведений о ключах
    assert user.http.get("/admin/llm/providers").status_code == 403


def test_fr030_external_provider_requires_https_and_key(admin):
    r = admin.post("/admin/llm/providers", json={
        "name": "QA-https", "url": "http://api.example.test/v1", "is_external": True, "api_key": EXT_KEY})
    assert r.status_code == 422 and "HTTPS" in r.text
    assert EXT_KEY not in r.text
    r = admin.post("/admin/llm/providers", json={
        "name": "QA-https", "url": "https://api.example.test/v1", "is_external": True})
    assert r.status_code == 422                                           # внешнему нужен ключ
    r = admin.post("/admin/llm/providers", json={
        "name": "QA-https", "url": "https://api.example.test/v1", "is_external": True, "api_key": EXT_KEY,
        "timeout_sec": 45, "model": "m"})
    assert r.status_code == 201, r.text
    p = r.json()
    try:
        assert p["has_key"] is True and p["is_external"] is True and p["timeout_sec"] == 45
        assert "api_key" not in p and EXT_KEY not in r.text                # ключ обратно не отдаётся
        listing = admin.http.get("/admin/llm/providers")
        assert EXT_KEY not in listing.text
        assert "api_key" not in json.dumps(listing.json())
        # смена внешнего на http отклоняется
        bad = admin.patch(f"/admin/llm/providers/{p['id']}", json={"url": "http://x.example.test/v1"})
        assert bad.status_code == 422
        # нельзя оставить внешнего без ключа
        nokey = admin.patch(f"/admin/llm/providers/{p['id']}", json={"remove_key": True})
        assert nokey.status_code == 422
        # смена ключа: has_key остаётся, значение не возвращается
        ch = admin.patch(f"/admin/llm/providers/{p['id']}", json={"api_key": EXT_KEY + "_2"})
        assert ch.status_code == 200 and ch.json()["has_key"] is True and EXT_KEY not in ch.text
    finally:
        assert admin.http.delete(f"/admin/llm/providers/{p['id']}", headers=admin.csrf()).status_code in (204, 409)


@pytest.mark.parametrize("payload,code", [
    ({"name": "QA-bad", "url": "ftp://x", "is_external": False}, 422),
    ({"name": "", "url": "http://fake-llm:8080/v1", "is_external": False}, 422),
    ({"name": "QA-bad", "url": "http://fake-llm:8080/v1", "is_external": False, "timeout_sec": 0}, 422),
    ({"name": "QA-bad", "url": "http://fake-llm:8080/v1", "is_external": False, "timeout_sec": 3601}, 422),
])
def test_fr030_provider_validation(admin, payload, code):
    assert admin.post("/admin/llm/providers", json=payload).status_code == code


def test_fr030_duplicate_name_and_default_switch(admin, slow_provider):
    dup = admin.post("/admin/llm/providers", json={
        "name": "QA-slow", "url": FAKE_LLM_INTERNAL, "is_external": False})
    assert dup.status_code == 409
    default = next(p for p in admin.http.get("/admin/llm/providers").json() if p["is_default"])
    assert admin.http.delete(f"/admin/llm/providers/{default['id']}", headers=admin.csrf()).status_code == 409
    # смена умолчания и возврат
    assert admin.patch(f"/admin/llm/providers/{slow_provider['id']}", json={"is_default": True}).status_code == 200
    try:
        now = {p["id"]: p["is_default"] for p in admin.http.get("/admin/llm/providers").json()}
        assert now[slow_provider["id"]] is True and now[default["id"]] is False
    finally:
        assert admin.patch(f"/admin/llm/providers/{default['id']}", json={"is_default": True}).status_code == 200


# --- FR-023: внутренний провайдер - без обезличивания; внешний - предпросмотр, подтверждение ----

def test_fr023_internal_provider_text_sent_as_is_with_env_key(user, llm):
    prompt = f"{MARKER} Иван Петров из ООО «Ромашка» просит оценить {MARKER}"
    r = submit(user, prompt)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["is_external"] is False and t["requires_confirmation"] is False and t["status"] in ("queued", "running")
    done = wait_task(user, t["id"])
    assert done["status"] == "done" and done["result"].startswith("Принято")
    w = wire(llm)
    assert len(w) == 1 and w[0]["prompt"] == prompt                       # критерий 3: внутренний - без изменений
    assert w[0]["auth"] == f"Bearer {CFG['LLM_API_KEY']}"                 # ключ внутренней Qwen из env
    assert w[0]["model"] == CFG["LLM_MODEL"]


def test_fr023_external_requires_confirmation_nothing_sent_before(user, ext_provider, llm):
    prompt = f"{MARKER} Контакт: Максим Рогожин, a.rogozhin@client-one.test, тел. +7 (913) 555-01-02"
    r = submit(user, prompt, provider_id=ext_provider["id"],
               terms={"persons": ["Максим Рогожин"], "companies": [], "projects": [], "locations": []})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["status"] == "awaiting_confirmation" and t["requires_confirmation"] is True and t["editable"]
    pv = t["preview"]
    for secret in ("Максим", "Рогожин", "a.rogozhin@client-one.test", "555-01-02"):
        assert secret not in pv["prompt"], secret
    assert MARKER in pv["prompt"] and "[" in pv["prompt"]
    assert pv["replacements"] and sum(pv["replacements"].values()) >= 3
    time.sleep(6)                                                         # > интервала опроса worker (2 с)
    assert wire(llm) == []                                                # до подтверждения ничего не ушло
    assert user.http.get(f"/llm/tasks/{t['id']}").json()["status"] == "awaiting_confirmation"
    c = user.post(f"/llm/tasks/{t['id']}/confirm")
    assert c.status_code == 200
    done = wait_task(user, t["id"])
    assert done["status"] == "done"
    w = wire(llm)
    assert len(w) == 1
    assert w[0]["prompt"] == pv["prompt"]                                 # предпросмотр == отправленному
    assert w[0]["auth"] == f"Bearer {EXT_KEY}"
    # ответ LLM содержит плейсхолдеры, пользователю они возвращены к исходным значениям
    assert "[" not in done["result"] or "Упомянуты" in done["result"]
    assert "Максим Рогожин" in done["result"] and "a.rogozhin@client-one.test" in done["result"]
    assert user.post(f"/llm/tasks/{t['id']}/confirm").status_code == 409  # повторное подтверждение


def test_fr023_instrumental_case_surnames_are_masked(user, ext_provider):
    t = submit(user, f"{MARKER} Беседовал с Тимофеем Квашниным вчера.", provider_id=ext_provider["id"],
               terms={"persons": ["Квашнин Тимофей Львович"], "companies": [], "projects": [], "locations": []})
    if t.status_code == 201:
        user.post(f"/llm/tasks/{t.json()['id']}/cancel")
        assert "Квашниным" not in t.json()["preview"]["prompt"]
    else:
        assert t.status_code == 422                                        # допустимо: отправка запрещена


def test_fr023_first_name_form_not_mapped_to_other_person(user, ext_provider, llm):
    t = submit(user, f"{MARKER} Свяжитесь с Иваном Петровым", provider_id=ext_provider["id"],
               terms={"persons": ["Иван Петров"], "companies": [], "projects": [], "locations": []}).json()
    user.post(f"/llm/tasks/{t['id']}/confirm")
    done = wait_task(user, t["id"])
    assert "Иванов Пётр Сергеевич" not in (done["result"] or "")


def test_fr023_cancel_before_confirm_sends_nothing(user, ext_provider, llm):
    t = submit(user, f"{MARKER} Сергей Орлов, ООО «Вектор»", provider_id=ext_provider["id"],
               terms={"persons": ["Сергей Орлов"], "companies": ["Вектор"], "projects": [], "locations": []}).json()
    assert t["status"] == "awaiting_confirmation"
    assert user.post(f"/llm/tasks/{t['id']}/cancel").json()["status"] == "cancelled"
    assert user.post(f"/llm/tasks/{t['id']}/confirm").status_code == 409
    time.sleep(5)
    assert wire(llm) == []


def test_fr023_tasks_are_private_to_owner(user, ext_provider, admin):
    t = submit(user, f"{MARKER} Анна Белова", provider_id=ext_provider["id"],
               terms={"persons": ["Анна Белова"], "companies": [], "projects": [], "locations": []}).json()
    assert admin.http.get(f"/llm/tasks/{t['id']}").status_code == 404
    assert admin.post(f"/llm/tasks/{t['id']}/confirm").status_code == 404
    assert t["id"] not in {x["id"] for x in admin.http.get("/llm/tasks").json()}
    assert user.post(f"/llm/tasks/{t['id']}/cancel").status_code == 200
    assert user.http.get("/llm/tasks/not-a-uuid").status_code == 404


WIRE_BUGS: dict[str, str] = {}


@pytest.mark.parametrize("ex", [
    pytest.param(e, id=e["id"], marks=[pytest.mark.xfail(strict=True, reason=WIRE_BUGS[e["id"]])]
                 if e["id"] in WIRE_BUGS else [])
    for e in DATASET])
def test_fr023_dataset_on_the_wire_zero_leaks(user, ext_provider, llm, ex):
    """Критерий 1 FR-023 «на проводе»: весь стек (API -> NER -> шлюз -> worker -> провайдер); во внешний
    провайдер не уходит ни одна строка из ``forbidden``; технические термины ``keep`` сохраняются."""
    t = submit(user, ex["text"], provider_id=ext_provider["id"], terms=ex["dictionary"])
    assert t.status_code == 201, t.text
    t = t.json()
    pv = t["preview"]["prompt"]
    leaked_preview = [s for s in ex["forbidden"] if s.lower() in pv.lower()]
    assert leaked_preview == [], f"утечка в предпросмотре: {leaked_preview}"
    assert user.post(f"/llm/tasks/{t['id']}/confirm").status_code == 200
    done = wait_task(user, t["id"])
    assert done["status"] == "done"
    sent = wire(llm)[-1]["prompt"]
    assert sent == pv, "предпросмотр не равен отправленному"
    leaked = [s for s in ex["forbidden"] if s.lower() in sent.lower()]
    assert leaked == [], f"утечка на проводе: {leaked}"
    lost = [k for k in ex.get("keep", []) if k.lower() not in sent.lower()]
    assert lost == [], f"маскируются технические термины: {lost}"


# --- NFR-010: таймаут и повтор ---------------------------------------------------------------------

def test_nfr010_provider_timeout_then_retry_ok(user, slow_provider, llm):
    """Провайдер завис: статус timeout не раньше таймаута (5 с), понятное сообщение; «Повторить» -> done."""
    llm.mode("hang", 60)
    t0 = time.monotonic()
    t = submit(user, f"{MARKER} запрос на таймаут", provider_id=slow_provider["id"]).json()
    assert t["timeout_sec"] == 5
    done = wait_task(user, t["id"], timeout=40)
    elapsed = time.monotonic() - t0
    assert done["status"] == "timeout" and done["error_code"] == "timeout", done
    assert 5 <= elapsed <= 25, elapsed
    assert "не ответил" in done["error_message"] and "Повторите" in done["error_message"]
    assert done["result"] is None
    llm.mode("ok")
    r = user.post(f"/llm/tasks/{t['id']}/retry")
    assert r.status_code == 200, r.text
    again = wait_task(user, t["id"])
    assert again["status"] == "done" and again["attempts"] >= 2 and again["id"] == t["id"]
    print(f"NFR-010: таймаут сработал через {elapsed:.1f} с при настройке 5 с")


def test_nfr010_slow_but_within_timeout_is_ok(user, slow_provider, llm):
    llm.mode("slow", 3)
    t = submit(user, f"{MARKER} медленный ответ", provider_id=slow_provider["id"]).json()
    done = wait_task(user, t["id"], timeout=30)
    assert done["status"] == "done"


def test_nfr010_timeout_is_taken_from_provider_settings(admin, user, slow_provider, llm):
    assert admin.patch(f"/admin/llm/providers/{slow_provider['id']}", json={"timeout_sec": 3}).status_code == 200
    try:
        llm.mode("hang", 30)
        t = submit(user, f"{MARKER} таймаут 3 с", provider_id=slow_provider["id"]).json()
        assert t["timeout_sec"] == 3
        t0 = time.monotonic()
        done = wait_task(user, t["id"], timeout=30)
        assert done["status"] == "timeout" and time.monotonic() - t0 < 20
    finally:
        admin.patch(f"/admin/llm/providers/{slow_provider['id']}", json={"timeout_sec": 5})


def test_nfr010_default_timeout_is_120_in_provider_settings(admin):
    d = next(p for p in admin.http.get("/admin/llm/providers").json() if p["is_default"])
    assert d["timeout_sec"] == 120


@pytest.mark.skipif(os.environ.get("QA_RUN_SLOW") != "1", reason="медленный прогон 120 с: QA_RUN_SLOW=1")
def test_nfr010_slow_run_default_120_seconds_timeout(user, llm):
    """Один «медленный» прогон: таймаут по умолчанию 120 с у внутренней Qwen."""
    llm.mode("hang", 140)
    t0 = time.monotonic()
    t = submit(user, f"{MARKER} зависание 120 с").json()
    assert t["timeout_sec"] == 120
    done = wait_task(user, t["id"], timeout=170)
    elapsed = time.monotonic() - t0
    print(f"NFR-010 slow: timeout через {elapsed:.1f} с")
    assert done["status"] == "timeout" and 119 <= elapsed <= 150
    llm.mode("ok")
    assert user.post(f"/llm/tasks/{t['id']}/retry").status_code == 200
    assert wait_task(user, t["id"])["status"] == "done"


def test_fr030_provider_errors_are_user_readable_and_retryable(user, llm):
    llm.mode("error500")
    t = submit(user, f"{MARKER} ошибка провайдера").json()
    done = wait_task(user, t["id"])
    assert done["status"] == "failed" and done["error_code"] == "provider_error" and "500" in done["error_message"]
    llm.mode("http401")
    assert user.post(f"/llm/tasks/{t['id']}/retry").status_code == 200
    done = wait_task(user, t["id"])
    assert done["status"] == "failed" and done["error_code"] == "provider_auth"
    assert CFG["LLM_API_KEY"] not in json.dumps(done)
    llm.mode("ok")
    assert user.post(f"/llm/tasks/{t['id']}/retry").status_code == 200
    assert wait_task(user, t["id"])["status"] == "done"
    assert user.post(f"/llm/tasks/{t['id']}/retry").status_code == 409     # завершённую повторять нельзя


def test_fr030_task_validation(user):
    assert submit(user, "").status_code == 422
    assert user.post("/llm/tasks", json={"purpose": "Bad Purpose", "prompt": "x"}).status_code == 422
    assert submit(user, "x" * 200_001).status_code == 422
    assert submit(user, "x", provider_id=999999).status_code == 404
    assert Session().http.post("/llm/tasks", json={"purpose": "a", "prompt": "b"}).status_code in (401, 403)


# --- NFR-006: секреты после прогона LLM -------------------------------------------------------------

def _compose_log(tmp_path: Path) -> Path:
    names = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={COMPOSE_PROJECT}",
         "--format", "{{.Names}}"], capture_output=True, text=True, timeout=60).stdout.split()
    out = tmp_path / "stand.log"
    with out.open("w", encoding="utf-8") as f:
        for n in names:
            r = subprocess.run(["docker", "logs", n], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
            f.write(r.stdout + r.stderr)
    return out


def test_nfr006_llm_key_prompts_and_logs(user, ext_provider, llm, tmp_path):
    """NFR-006 для LLM: ключ внешнего провайдера и ключ внутренней Qwen не лежат открыто в БД и не
    попадают в логи; тексты запросов в БД - шифртекстом; ПДн из запроса не попадают в логи."""
    pii = f"Тимур Лебедев {MARKER}"
    t = submit(user, f"Позвоните {pii} по телефону +7 913 555 77 88", provider_id=ext_provider["id"],
               terms={"persons": ["Тимур Лебедев"], "companies": [], "projects": [], "locations": []}).json()
    pending = submit(user, f"Ждёт подтверждения {MARKER} Олег Громов", provider_id=ext_provider["id"],
                     terms={"persons": ["Олег Громов"], "companies": [], "projects": [], "locations": []}).json()
    user.post(f"/llm/tasks/{t['id']}/confirm")
    wait_task(user, t["id"])
    with db("app_db") as c:
        # ключ провайдера - только в secret_store шифртекстом; в llm_provider столбца ключа нет
        cols = {r[0] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='app' AND table_name='llm_provider'")}
        assert not any("key" in x or "secret" in x for x in cols), cols
        row = c.execute("SELECT length(ciphertext) FROM app.secret_store WHERE kind='llm_key' AND provider_id=%s",
                        (ext_provider["id"],)).fetchone()
        assert row and row[0] > len(EXT_KEY)
        # ожидающая задача: payload - шифртекст (nonce 12 байт), открытого текста нет
        p = c.execute("SELECT payload_ct, octet_length(payload_nonce) FROM app.llm_task WHERE id=%s",
                      (pending["id"],)).fetchone()
        assert p[0] is not None and p[1] == 12
        assert MARKER.encode() not in bytes(p[0]) and "Олег".encode() not in bytes(p[0])
        # завершённая: payload очищен, результат шифртекстом
        d = c.execute("SELECT payload_ct, result_ct FROM app.llm_task WHERE id=%s", (t["id"],)).fetchone()
        assert d[0] is None and d[1] is not None and MARKER.encode() not in bytes(d[1])
    user.post(f"/llm/tasks/{pending['id']}/cancel")
    # скрипт NFR-006: БД и логи с канарейками (ключи и фрагменты запросов)
    script = ROOT / "qa" / "nfr006_check.py"
    canaries = [EXT_KEY, CFG["LLM_API_KEY"], MARKER, "Тимур Лебедев", "555 77 88"]
    env = {**os.environ, "NFR006_CANARIES": ",".join(canaries), "PYTHONIOENCODING": "utf-8"}
    dburl = f"postgresql://app_rw:{CFG['APP_RW_PASSWORD']}@127.0.0.1:{CFG.get('QA_DB_PORT', '15432')}/app_db"
    r = subprocess.run([sys.executable, str(script), "--db-url", dburl], capture_output=True, text=True,
                       encoding="utf-8", env=env, timeout=300)
    assert r.returncode == 0, r.stdout[-1500:]
    log = _compose_log(tmp_path)
    text = log.read_text(encoding="utf-8", errors="replace")
    api_part = "\n".join(line for line in text.splitlines()
                         if "fake-llm" not in line)                       # журнал самого фейка - не приложение
    for c in canaries:
        assert c not in api_part, f"канарейка {c[:6]}... найдена в логах стенда"
    dump = subprocess.run(
        ["docker", "exec", f"{COMPOSE_PROJECT}-db-1", "pg_dump", "-U", "postgres", "-d", "app_db", "--inserts"],
        capture_output=True, timeout=120).stdout
    for c in canaries:
        assert c.encode() not in dump and c.encode().hex().encode() not in dump.lower(), c[:6]
