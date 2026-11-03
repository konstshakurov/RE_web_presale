"""API «CV по шаблону клиента» (FR-017, KON-80): маршруты на TestClient.

БД не нужна: CvRepo/шлюз/потребности/лимиты подменены памятью. Гоняется полный контракт:
контекст, генерация (внутренний и внешний провайдер), сохранение файла, письмо.
Правило с 03.11.2026: новый функционал QA-агент проверяет на локальном стенде до деплоя.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.sessions import csrf_token, hash_session_id, new_session_id
from app.config import Secret, Settings
from app.cvtpl.router import router as cvtpl_router
from app.cvtpl.service import CvTplService

KEY32 = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # base64(b'0123...' 32 байта)
NEED = "ANALYTICS-5938"
EMP = "ANALYTICS-96"
CLIENT = "SA-4160"
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
RESULT = '{"cv":"CV-текст","recommendations":["r1"],"letter":"Письмо"}'


def build_docx(text: str) -> bytes:
    """Минимальный docx (копия cvtpl.docxgen) - extract_text умеет читать."""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in text.splitlines())
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


class _View:
    def __init__(self, v: dict) -> None:
        self.__dict__.update(v)


class FakeGateway:
    """submit/get с памятью; mark() - управление сценариями тестов."""

    def __init__(self) -> None:
        self.views: dict[str, dict] = {}
        self.seq = 0

    def submit(self, owner_login, purpose, prompt, *, system=None, provider_id=None,
               ref=None, terms=None):
        self.seq += 1
        tid = f"task-{self.seq}"
        is_ext = provider_id == 2
        self.views[tid] = {
            "id": tid, "purpose": purpose, "ref": ref, "owner": owner_login,
            "status": "awaiting_confirmation" if is_ext else "queued",
            "requires_confirmation": is_ext, "provider_id": provider_id or 1,
            "result": None, "error_code": None, "error_message": None,
        }
        return _View(self.views[tid])

    def get(self, owner_login, task_id):
        v = self.views.get(task_id)
        if v is None or v["owner"] != owner_login:
            return _View({"id": task_id, "purpose": "other", "owner": "?",
                          "status": "cancelled", "requires_confirmation": False,
                          "provider_id": 0, "result": None})
        return _View(v)

    def mark(self, task_id: str, **kw) -> None:
        self.views[task_id].update(kw)


class FakeNeedSource:
    def find_need(self, key):
        if key != NEED:
            return None
        return type("NeedInfo", (), {"title": "Аналитик Банк", "epic_key": CLIENT})


class FakeCvRepo:
    """Файлы хранятся как объекты-строки с полями FileRow (сервис читает атрибуты)."""

    def __init__(self) -> None:
        self.seq = 0
        self.tick = T0
        self.employees = {EMP}
        self.files: dict[int, object] = {}

    def employee_exists(self, key):
        return key in self.employees

    def list_files(self, employee_key):
        return [f for f in self.files.values() if f.employee_key == employee_key]

    def current_main(self, key):
        return next((f for f in self.files.values()
                     if f.employee_key == key and f.kind == "main_cv"
                     and not f.is_archived), None)

    def get_file(self, file_id):
        return self.files.get(file_id)

    def get_content(self, file_id):
        f = self.files.get(file_id)
        return (f, f.content) if f else None

    def add_file(self, *, employee_key, kind, file_name, mime_type, content, uploaded_by,
                 extracted_text, extraction_status, client_epic_key=None):
        self.seq += 1
        self.tick += timedelta(seconds=1)
        file_id = 100 + self.seq
        self.files[file_id] = _R(id=file_id, employee_key=employee_key, kind=kind,
                                 client_epic_key=client_epic_key, file_name=file_name,
                                 is_archived=False, uploaded_at=self.tick, content=content,
                                 extracted_text=extracted_text, extraction_status=extraction_status)
        return file_id


class _R:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


class FakeLimits:
    def __init__(self, mb: int = 10) -> None:
        self.mb = mb

    def max_mb(self) -> int:
        return self.mb


class FakeSession:
    def status(self, login):
        return type("I", (), {"state": "ok", "code": "", "message": ""})()

    def get_user_jira_token(self, login):
        return "sealed-token"


class FakeJira:
    def __init__(self) -> None:
        self.comments: list[tuple[str | None, str]] = []

    def add_comment(self, sealed_token, issue_key, body):
        self.comments.append((issue_key, body))


class _FakePool:
    """Заглушка пула: EpicNames обещает None-имена (fallback на ключ) без БД."""

    class _Conn:
        def execute(self, *a, **kw):
            class _R:
                def fetchone(self):
                    return None

                def fetchall(self):
                    return []

            return _R()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def connection(self):
        return self._Conn()


@pytest.fixture()
def env():
    gw, repo = FakeGateway(), FakeCvRepo()
    # Основное CV сотрудника (docx-сигнатура; extract_text подменяется на уровне сервиса тестом?
    # Нет: extract работает по содержимому - подсовываем распаковываемый docx из _cvtpl-хелперов ниже.)
    repo.files[7] = _R(id=7, employee_key=EMP, kind="main_cv", client_epic_key=None,
                       file_name="CV_Основной.docx", is_archived=False, uploaded_at=T0,
                       content=build_docx("Иван: опыт, проекты, навыки"),
                       extracted_text="Иван: опыт, проекты, навыки",
                       extraction_status="ok")
    app = FastAPI()
    app.include_router(cvtpl_router)
    app.state.cvtpl = CvTplService(gw, repo, FakeNeedSource(), limit_mb=10)
    app.state.cv = repo
    app.state.cv_limits = FakeLimits(10)
    app.state.pool = _FakePool()
    app.state.jira = FakeJira()
    app.state.jira_session = FakeSession()
    app.state.settings = Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(KEY32))
    app.state.repo = _AuthRepo()
    sid = new_session_id()
    app.state.repo.sessions[hash_session_id(sid)] = "ivan"
    client = TestClient(app)
    client.cookies.set("re_session", sid)
    headers = {"X-CSRF-Token": csrf_token(app.state.settings.enc_key_bytes(), sid)}
    return client, headers, gw, repo


class _AuthRepo:
    """Минимальный AppRepo-подобраз для auth.deps (сессия "ivan", role user)."""

    def __init__(self) -> None:
        self.sessions: dict[bytes, str] = {}

    def get_user(self, login):
        return type("U", (), {"ldap_login": login, "role": "user", "active": True,
                              "display_name": None})()

    def get_active_session(self, h):
        return (self.sessions[h], T0) if h in self.sessions else None

    def touch_session(self, h): ...

    def revoke_session(self, h): ...


def _gen_file(name="Шаблон.docx", body=b"PK\x03\x04fake") -> bytes:
    return body


# --- тесты ---


def test_context_ok(env):
    client, headers, gw, repo = env
    _login_none = None  # auth через cookie: TestClient с сессией уже установлен в fixture
    r = client.get(f"/cvtpl/context?need_key={NEED}&employee_key={EMP}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["client_epic_key"] == CLIENT
    assert d["main_cv"]["file_name"] == "CV_Основной.docx"
    assert d["repeat"] is None


def test_context_need_not_found(env):
    client, headers, gw, repo = env
    r = client.get("/cvtpl/context?need_key=ANALYTICS-1111&employee_key=" + EMP)
    assert r.status_code == 404


def test_generate_internal_ok(env):
    client, headers, gw, repo = env
    r = client.post(
        f"/cvtpl/generate?need_key={NEED}&employee_key={EMP}"
        "&template_name=Шаблон.docx&main_cv_file_id=7&provider_id=1",
        headers=headers,
        content=build_docx("Шаблон: опыт, проекты, навыки"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["requires_confirmation"] is False

