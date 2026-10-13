"""Управление фейками QA-стенда и доступ к БД стенда для тестов этапа 1 (KON-47).

Фейк Jira (``qa/stand/fake_jira.py``) и фейк LLM (``qa/stand/fake_llm.py``) управляются по HTTP на loopback
(``QA_FAKE_JIRA_URL``, ``QA_FAKE_LLM_URL`` из ``qa/stand/.qa.env``). Без них тесты пропускаются.
Подключение к БД - ролями приложения (``app_rw``, ``cv_rw``) на ``127.0.0.1:QA_DB_PORT``: только то, что
доступно самому приложению. Значения секретов в вывод не попадают.
"""
from __future__ import annotations

import io
import time
import zipfile
from contextlib import contextmanager

import httpx
import pytest

from ._stand import CFG, requires_stand

JIRA_URL = CFG.get("QA_FAKE_JIRA_URL", "http://127.0.0.1:18090")
LLM_URL = CFG.get("QA_FAKE_LLM_URL", "http://127.0.0.1:18091")
DB_PORT = CFG.get("QA_DB_PORT", "15432")
#: ключ «внешнего» провайдера в тестах: заведомо фиктивная строка (канарейка NFR-006)
EXT_KEY = "FAKE_ext_provider_key_QA47"


def _alive(url: str, path: str) -> bool:
    try:
        return httpx.get(url + path, timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def fakes_available() -> bool:
    return _alive(JIRA_URL, "/__qa/stats") and _alive(LLM_URL, "/__qa/stats")


requires_fakes = pytest.mark.skipif(
    not (fakes_available()), reason="фейки Jira/LLM недоступны: bash qa/stand/stand.sh up (KON-47)")
stand_and_fakes = [requires_stand, requires_fakes]


class JiraCtl:
    def __init__(self) -> None:
        self.http = httpx.Client(base_url=JIRA_URL, timeout=30)

    def reset(self) -> None:
        self.http.post("/__qa/reset", json={}).raise_for_status()

    def set_status(self, key: str, status: str) -> None:
        self.http.post("/__qa/status", json={"key": key, "status": status}).raise_for_status()

    def age(self, minutes: float = 10) -> None:
        self.http.post("/__qa/age", json={"minutes": minutes}).raise_for_status()

    def gen(self, count: int, start: int = 20000, status: str = "mixed") -> None:
        self.http.post("/__qa/gen", json={"count": count, "start": start, "status": status},
                       timeout=120).raise_for_status()

    def fail(self, mode: str = "none", delay: float = 0.0) -> None:
        self.http.post("/__qa/fail", json={"mode": mode, "delay": delay}).raise_for_status()

    def stats(self) -> dict:
        return self.http.get("/__qa/stats").json()


class LlmCtl:
    def __init__(self) -> None:
        self.http = httpx.Client(base_url=LLM_URL, timeout=30)

    def reset(self) -> None:
        self.http.post("/__qa/reset", json={}).raise_for_status()

    def mode(self, mode: str = "ok", delay: float = 0.0) -> None:
        self.http.post("/__qa/mode", json={"mode": mode, "delay": delay}).raise_for_status()

    def captured(self) -> list[dict]:
        return self.http.get("/__qa/captured").json()

    def stats(self) -> dict:
        return self.http.get("/__qa/stats").json()


def db_url(db: str) -> str:
    user, key = ("app_rw", "APP_RW_PASSWORD") if db == "app_db" else ("cv_rw", "CV_RW_PASSWORD")
    return f"postgresql://{user}:{CFG[key]}@127.0.0.1:{DB_PORT}/{db}"


@contextmanager
def db(name: str):
    import psycopg

    with psycopg.connect(db_url(name), connect_timeout=5, autocommit=True) as conn:
        yield conn


@contextmanager
def db_admin(name: str):
    """Суперпользователь БД стенда: только для очистки тестовых данных (триггер защиты основного CV)."""
    import psycopg

    url = f"postgresql://postgres:{CFG['POSTGRES_PASSWORD']}@127.0.0.1:{DB_PORT}/{name}"
    with psycopg.connect(url, connect_timeout=5, autocommit=True) as conn:
        yield conn


def wait_until(fn, timeout: float = 60.0, interval: float = 1.0, what: str = "условие"):
    """Опрос до истинного значения; возвращает его. По таймауту - AssertionError с названием условия."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"не дождались: {what} за {timeout:g} с (последнее значение: {last!r})")


# --- файлы для тестов (генерируются в памяти, без реальных данных) ---

def make_docx(*paragraphs: str) -> bytes:
    """Минимальный валидный DOCX: только word/document.xml (достаточно для извлечения текста)."""
    from xml.sax.saxutils import escape

    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002"
    "000001e221bc330000000049454e44ae426082")
PDF_MIN = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
