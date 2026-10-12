"""API файлов CV: правила загрузки, права, скачивание, удаление (FR-022, FR-039, FR-040).
БД не нужна: репозиторий подменён памятью. Поведение БД - в test_cv_db.py.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cv.repo import NOT_ARCHIVED, NOT_FOUND, OK, PROTECTED, FileRow
from app.cv.router import router as cv_router

from ._cv_fakes import build_docx, make_client

EMP = "EMPT-1"
MB = 1024 * 1024


class FakeCvRepo:
    """Память вместо cv_db; правила архива повторяют cv.replace_main_cv."""

    def __init__(self) -> None:
        self.files: dict[int, FileRow] = {}
        self.blobs: dict[int, bytes] = {}
        self.texts: dict[int, str | None] = {}
        self.seq = 0
        self.tick = datetime(2026, 10, 1, tzinfo=UTC)
        self.employees = {EMP}

    def employee_exists(self, key):
        return key in self.employees

    def list_files(self, employee_key=None, *, include_archived=False):
        rows = [f for f in self.files.values()
                if (employee_key is None or f.employee_key == employee_key)
                and (include_archived or not f.is_archived)]
        # как в SQL: свежие сверху (архив - по archived_at)
        return sorted(rows, key=lambda f: f.archived_at or f.uploaded_at, reverse=True)

    def get_file(self, file_id):
        return self.files.get(file_id)

    def get_content(self, file_id):
        f = self.files.get(file_id)
        return (f, self.blobs[file_id]) if f else None

    def get_text(self, file_id):
        f = self.files.get(file_id)
        return (f, self.texts[file_id]) if f else None

    def current_main(self, key):
        return next((f for f in self.files.values()
                     if f.employee_key == key and f.is_current_main), None)

    def add_file(self, *, employee_key, kind, file_name, mime_type, content, uploaded_by,
                 extracted_text, extraction_status):
        self.seq += 1
        self.tick += timedelta(seconds=1)
        if kind == "main_cv":
            cur = self.current_main(employee_key)
            if cur:
                self.files[cur.id] = replace(cur, is_archived=True, archived_at=self.tick)
            old = sorted((f for f in self.files.values() if f.employee_key == employee_key
                          and f.kind == "main_cv" and f.is_archived),
                         key=lambda f: f.archived_at, reverse=True)
            for f in old[3:]:
                del self.files[f.id]
        self.files[self.seq] = FileRow(
            self.seq, employee_key, kind, None, file_name, mime_type, len(content), False, None,
            extraction_status, uploaded_by, self.tick)
        self.blobs[self.seq], self.texts[self.seq] = content, extracted_text
        return self.seq

    def delete_file(self, file_id):
        f = self.files.get(file_id)
        if f is None:
            return NOT_FOUND
        if f.is_current_main:
            return PROTECTED
        del self.files[file_id]
        return OK

    def restore_main(self, file_id):
        f = self.files.get(file_id)
        if f is None:
            return NOT_FOUND
        if not (f.kind == "main_cv" and f.is_archived):
            return NOT_ARCHIVED
        cur = self.current_main(f.employee_key)
        self.files[cur.id] = replace(cur, is_archived=True, archived_at=self.tick)
        self.files[file_id] = replace(f, is_archived=False, archived_at=None)
        return OK


class FakeLimits:
    def __init__(self, mb=10):
        self.mb = mb

    def max_mb(self):
        return self.mb

    def max_bytes(self):
        return self.mb * MB

    def set_max_mb(self, v):
        self.mb = v


@pytest.fixture()
def env():
    repo, limits = FakeCvRepo(), FakeLimits()
    client, headers, app = make_client(repo, limits)
    return client, headers, repo, limits, app


def upload(client, headers, name="cv.docx", kind="main_cv", body=None, emp=EMP, **kw):
    body = build_docx(["SQL, UML"]) if body is None else body
    params = {"file_name": name}
    if kind is not None:
        params["kind"] = kind
    return client.post(f"/cv/employees/{emp}/files", params=params, content=body,
                       headers=kw.get("headers", headers))


def test_requires_login():
    app = FastAPI()
    app.include_router(cv_router)
    c = TestClient(app)
    assert c.get("/cv/files").status_code == 401
    assert c.get("/cv/limits").status_code == 401
    assert c.post("/cv/employees/X-1/files?kind=main_cv&file_name=a.docx", content=b"x"
                  ).status_code == 401
    assert c.delete("/cv/files/1").status_code == 401


def test_mutations_need_csrf(env):
    client, _, *_ = env
    assert upload(client, {}).status_code == 403
    assert client.delete("/cv/files/1").status_code == 403
    assert client.post("/cv/files/1/restore").status_code == 403


def test_upload_without_kind_is_rejected(env):
    """FR-022 п. 1: без выбора типа загрузка невозможна."""
    client, headers, repo, *_ = env
    assert upload(client, headers, kind=None).status_code == 422
    assert upload(client, headers, kind="client_template_cv").status_code == 422  # только система
    assert repo.files == {}


def test_upload_main_cv_extracts_text_and_shows_in_list(env):
    client, headers, repo, *_ = env
    r = upload(client, headers)
    assert r.status_code == 201
    body = r.json()
    f = body["file"]
    assert f["kind"] == "main_cv" and f["kind_title"] == "Основное CV"
    assert f["extraction_status"] == "ok" and f["can_delete"] is False
    assert f["uploaded_by"] == "ivan" and body["replaced_file_id"] is None
    listing = client.get(f"/cv/employees/{EMP}/files").json()
    assert [x["id"] for x in listing["files"]] == [f["id"]] and listing["archive"] == []
    text = client.get(f"/cv/employees/{EMP}/main-cv").json()
    assert text["extraction_status"] == "ok" and text["text"] == "SQL, UML"
    assert text["file"]["file_name"] == "cv.docx"
    assert client.get("/cv/files").json()["items"][0]["id"] == f["id"]


def test_upload_extract_error_keeps_file_with_message(env):
    client, headers, *_ = env
    r = upload(client, headers, name="cv.pdf", body=b"%PDF-1.4 x")
    assert r.status_code == 201
    f = r.json()["file"]
    assert f["extraction_status"] == "error" and "не поддерживается" in f["extraction_message"]
    cur = client.get(f"/cv/employees/{EMP}/main-cv").json()
    assert cur["extraction_status"] == "error" and cur["text"] is None and cur["message"]


def test_materials_are_not_extracted(env):
    client, headers, *_ = env
    r = upload(client, headers, name="demo.pptx", kind="materials", body=b"PK\x03\x04zz")
    f = r.json()["file"]
    assert f["extraction_status"] == "none" and f["extraction_message"] is None
    assert f["can_delete"] is True


def test_invalid_format_rejected_and_not_stored(env):
    client, headers, repo, *_ = env
    for name, kind in [("run.exe", "extra_cv"), ("cv", "main_cv"), ("a.png", "main_cv"),
                       ("a.xlsx", "extra_cv")]:
        r = upload(client, headers, name=name, kind=kind, body=b"MZ....")
        assert r.status_code == 415, name
    # содержимое не соответствует расширению
    assert upload(client, headers, name="a.docx", body=b"hello").status_code == 415
    assert repo.files == {}


def test_size_limit_from_setting(env):
    """FR-022 п. 3: больше лимита - отказ, в карточку не попадает."""
    client, headers, repo, limits, _ = env
    limits.mb = 1
    big = build_docx(["x"]) + b"0" * MB
    r = upload(client, headers, body=big)
    assert r.status_code == 413 and "1 МБ" in r.json()["detail"]
    assert repo.files == {}
    limits.mb = 2
    assert upload(client, headers, body=big).status_code == 201


def test_empty_and_unknown_employee(env):
    client, headers, *_ = env
    assert upload(client, headers, body=b"").status_code == 422
    assert upload(client, headers, emp="NOPE-9").status_code == 404
    assert client.get("/cv/employees/NOPE-9/files").status_code == 404
    assert client.get("/cv/employees/NOPE-9/main-cv").status_code == 404


def test_main_cv_none_status_when_no_file(env):
    client, *_ = env
    r = client.get(f"/cv/employees/{EMP}/main-cv")
    assert r.status_code == 200
    assert r.json()["extraction_status"] == "none" and r.json()["file"] is None


def test_replace_keeps_3_versions_and_delete_protection(env):
    client, headers, repo, *_ = env
    ids = []
    for i in range(5):
        r = upload(client, headers, name=f"cv{i}.docx", body=build_docx([f"v{i}"]))
        ids.append(r.json()["file"]["id"])
    listing = client.get(f"/cv/employees/{EMP}/files").json()
    assert [f["id"] for f in listing["files"]] == [ids[4]]
    assert [f["id"] for f in listing["archive"]] == [ids[3], ids[2], ids[1]]  # 3 свежие
    assert all(f["can_restore"] and f["is_archived"] for f in listing["archive"])
    r = client.delete(f"/cv/files/{ids[4]}", headers=headers)
    assert r.status_code == 409 and "заменить" in r.json()["detail"]


def test_restore_swaps_current_and_archive(env):
    client, headers, *_ = env
    a = upload(client, headers, name="a.docx", body=build_docx(["A"])).json()["file"]["id"]
    b = upload(client, headers, name="b.docx", body=build_docx(["B"])).json()["file"]["id"]
    r = client.post(f"/cv/files/{a}/restore", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert [f["id"] for f in body["files"]] == [a]
    assert [f["id"] for f in body["archive"]] == [b]
    # актуальное восстановить нельзя; нет файла - 404
    assert client.post(f"/cv/files/{a}/restore", headers=headers).status_code == 409
    assert client.post("/cv/files/999/restore", headers=headers).status_code == 404


def test_delete_extra_and_materials(env):
    client, headers, repo, *_ = env
    extra = upload(client, headers, name="x.docx", kind="extra_cv").json()["file"]["id"]
    mat = upload(client, headers, name="m.png", kind="materials", body=b"\x89PNG")
    mat = mat.json()["file"]["id"]
    for fid in (extra, mat):
        assert client.delete(f"/cv/files/{fid}", headers=headers).status_code == 204
        assert client.get(f"/cv/files/{fid}/download").status_code == 404
    assert client.delete(f"/cv/files/{extra}", headers=headers).status_code == 404
    assert client.get(f"/cv/employees/{EMP}/files").json()["files"] == []


def test_download_returns_original_bytes_with_safe_headers(env):
    client, headers, *_ = env
    body = build_docx(["Файл"])
    fid = upload(client, headers, name="Резюме 2026.docx", body=body).json()["file"]["id"]
    r = client.get(f"/cv/files/{fid}/download")
    assert r.status_code == 200
    assert hashlib.sha256(r.content).digest() == hashlib.sha256(body).digest()
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    disp = r.headers["content-disposition"]
    assert disp.startswith("attachment;") and "filename*=UTF-8''" in disp
    assert "%D0%A0" in disp and r.headers["x-content-type-options"] == "nosniff"


def test_file_text_endpoint(env):
    client, headers, *_ = env
    fid = upload(client, headers, body=build_docx(["Опыт Python"])).json()["file"]["id"]
    r = client.get(f"/cv/files/{fid}/text").json()
    assert r["text"] == "Опыт Python" and r["file"]["id"] == fid
    assert client.get("/cv/files/404/text").status_code == 404


def test_limits_read_and_admin_only_write(env):
    client, headers, _, limits, app = env
    r = client.get("/cv/limits").json()
    assert r["max_file_mb"] == 10 and r["max_file_bytes"] == 10 * MB
    assert "docx" in r["cv_extensions"] and r["upload_kinds"]["main_cv"] == "Основное CV"
    assert client.put("/cv/limits", json={"max_file_mb": 5}, headers=headers).status_code == 403
    admin, ah, _ = make_client(env[2], limits, role="admin")
    assert admin.put("/cv/limits", json={"max_file_mb": 5}, headers=ah).json()["max_file_mb"] == 5
    assert admin.put("/cv/limits", json={"max_file_mb": 11}, headers=ah).status_code == 422
    assert admin.put("/cv/limits", json={"max_file_mb": 0}, headers=ah).status_code == 422


def test_store_not_configured_gives_503():
    client, headers, _ = make_client(None, FakeLimits())
    assert client.get("/cv/files").status_code == 503
    assert upload(client, headers).status_code == 503
