"""Файлы CV на настоящей БД (KON-36): архив 3 версий, восстановление, защита основного CV,
скачивание без изменений, лимит из app_setting, сквозной сценарий через API.

Нужны TEST_CV_DB_URL (cv_rw, миграции cv_db V001-V006) и TEST_APP_DB_URL (app_rw, app_db
V001-V007); без них пропуск. Данные тестов: сотрудники с ключами KONT36-*, после теста удаляются.
"""
from __future__ import annotations

import hashlib
import os

import psycopg
import pytest

from app.app_repo import create_pool
from app.cv.limits import CvLimits
from app.cv.repo import NOT_ARCHIVED, NOT_FOUND, OK, PROTECTED, CvRepo

from ._cv_fakes import build_doc, build_docx, make_client

CV_DSN = os.environ.get("TEST_CV_DB_URL")
APP_DSN = os.environ.get("TEST_APP_DB_URL")
pytestmark = pytest.mark.skipif(
    not (CV_DSN and APP_DSN), reason="TEST_CV_DB_URL / TEST_APP_DB_URL не заданы"
)
E1, E2 = "KONT36-1", "KONT36-2"
MB = 1024 * 1024


@pytest.fixture()
def env():
    cv_pool, app_pool = create_pool(CV_DSN), create_pool(APP_DSN)
    cv_pool.open(wait=True)
    app_pool.open(wait=True)
    _purge(cv_pool)
    with cv_pool.connection() as c:
        for k in (E1, E2):
            c.execute(
                "INSERT INTO cv.employee (jira_key, full_name, status, assignable) "
                "VALUES (%s, %s, 'Работает', true)", (k, f"Тестовый сотрудник {k}"))
    with app_pool.connection() as c:
        c.execute("DELETE FROM app.app_setting WHERE key = 'max_file_mb'")
    yield CvRepo(cv_pool), CvLimits(app_pool), cv_pool, app_pool
    _purge(cv_pool)
    with app_pool.connection() as c:
        c.execute("DELETE FROM app.app_setting WHERE key = 'max_file_mb'")
    cv_pool.close()
    app_pool.close()


def _purge(pool):
    """Тестовые файлы удаляются в обход защиты: актуальное основное CV сначала уходит в архив
    (после удаления прочих версий, чтобы не нарушить лимит архива)."""
    with pool.connection() as c:
        c.execute("DELETE FROM cv.employee_file WHERE employee_key LIKE 'KONT36-%' "
                  "AND (is_archived OR kind <> 'main_cv')")
        c.execute(
            "UPDATE cv.employee_file SET is_archived = true, archived_at = now() "
            "WHERE employee_key LIKE 'KONT36-%' AND kind = 'main_cv' AND NOT is_archived")
        c.execute("DELETE FROM cv.employee_file WHERE employee_key LIKE 'KONT36-%'")
        c.execute("DELETE FROM cv.employee WHERE jira_key LIKE 'KONT36-%'")


def add(repo, emp=E1, kind="main_cv", name="cv.docx", content=None, text="t", status="ok"):
    return repo.add_file(
        employee_key=emp, kind=kind, file_name=name, mime_type="application/x",
        content=content or build_docx(["x"]), uploaded_by="ivan",
        extracted_text=text, extraction_status=status)


def test_replace_main_cv_archive_limit_3(env):
    """FR-039 п. 1-3: одно актуальное, прежнее в архиве, архив не больше 3."""
    repo, *_ = env
    ids = [add(repo, name=f"v{i}.docx", content=build_docx([f"v{i}"])) for i in range(5)]
    files = repo.list_files(E1, include_archived=True)
    current = [f for f in files if not f.is_archived and f.kind == "main_cv"]
    archive = [f for f in files if f.is_archived]
    assert [f.id for f in current] == [ids[4]]
    assert [f.id for f in archive] == [ids[3], ids[2], ids[1]]  # самая старая вытеснена
    assert repo.get_file(ids[0]) is None and repo.get_content(ids[0]) is None


def test_restore_makes_it_current_and_current_goes_to_archive(env):
    repo, *_ = env
    a, b, c = (add(repo, name=f"v{i}.docx") for i in range(3))
    assert repo.restore_main(a) == OK
    assert repo.current_main(E1).id == a
    archived = {f.id for f in repo.list_files(E1, include_archived=True) if f.is_archived}
    assert archived == {b, c}
    # одно актуальное и после восстановления
    assert sum(1 for f in repo.list_files(E1) if f.kind == "main_cv") == 1
    assert repo.restore_main(a) == NOT_ARCHIVED
    assert repo.restore_main(10**9) == NOT_FOUND


def test_current_main_cannot_be_deleted_others_can(env):
    """FR-040 п. 3: актуальное основное CV не удаляется; доп. CV, материалы и архив - можно."""
    repo, *_ = env
    old = add(repo, name="old.docx")
    cur = add(repo, name="cur.docx")
    extra = add(repo, kind="extra_cv", name="e.docx")
    mat = add(repo, kind="materials", name="m.png", text=None, status="none")
    assert repo.delete_file(cur) == PROTECTED
    assert repo.get_file(cur) is not None
    assert repo.delete_file(extra) == OK and repo.delete_file(mat) == OK
    assert repo.delete_file(old) == OK  # архивная версия
    assert repo.delete_file(old) == NOT_FOUND
    assert [f.id for f in repo.list_files(E1, include_archived=True)] == [cur]


def test_db_trigger_is_backstop_for_direct_delete(env):
    repo, _, cv_pool, _ = env
    cur = add(repo)
    with pytest.raises(psycopg.errors.RestrictViolation):
        with cv_pool.connection() as c:
            c.execute("DELETE FROM cv.employee_file WHERE id = %s", (cur,))


def test_content_roundtrip_and_sha(env):
    repo, _, cv_pool, _ = env
    body = build_docx(["Содержимое"]) + b"\x00\xff" * 100
    fid = add(repo, kind="extra_cv", content=body)
    meta, got = repo.get_content(fid)
    assert got == body and meta.size_bytes == len(body)
    with cv_pool.connection() as c:
        sha = c.execute("SELECT sha256 FROM cv.employee_file WHERE id = %s", (fid,)).fetchone()[0]
    assert bytes(sha) == hashlib.sha256(body).digest()


def test_other_employees_are_independent(env):
    repo, *_ = env
    a = add(repo, emp=E1)
    b = add(repo, emp=E2)
    assert repo.current_main(E1).id == a and repo.current_main(E2).id == b
    add(repo, emp=E1, name="new.docx")
    assert repo.current_main(E2).id == b


def test_limits_setting_roundtrip_and_fallback(env):
    _, limits, _, app_pool = env
    assert limits.max_mb() == 10  # нет записи - умолчание
    limits.set_max_mb(3)
    assert limits.max_mb() == 3 and limits.max_bytes() == 3 * MB
    limits.set_max_mb(4)  # upsert
    assert limits.max_mb() == 4
    with app_pool.connection() as c:  # мусор и значение выше потолка БД игнорируются
        c.execute("UPDATE app.app_setting SET value = '99' WHERE key = 'max_file_mb'")
    assert limits.max_mb() == 10
    with pytest.raises(ValueError):
        limits.set_max_mb(11)


def test_end_to_end_through_api(env):
    """Сквозной сценарий: загрузка DOC и DOCX, замена, архив, восстановление, текст, скачивание."""
    repo, limits, *_ = env
    client, headers, _ = make_client(repo, limits)
    doc = build_doc("Аналитик, опыт SQL и UML", unicode=True, pad=True)

    def up(name, body, kind="main_cv"):
        r = client.post(f"/cv/employees/{E1}/files", params={"kind": kind, "file_name": name},
                        content=body, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()

    first = up("cv.doc", doc)["file"]
    assert first["extraction_status"] == "ok"
    assert client.get(f"/cv/employees/{E1}/main-cv").json()["text"] == "Аналитик, опыт SQL и UML"
    second = up("cv2.docx", build_docx(["Второе резюме"]))
    assert second["replaced_file_id"] == first["id"]
    up("proj.pptx", b"PK\x03\x04pp", kind="materials")
    up("scan.pdf", b"%PDF-1.4 scan", kind="materials")

    listing = client.get(f"/cv/employees/{E1}/files").json()
    assert {f["file_name"] for f in listing["files"]} == {"cv2.docx", "proj.pptx", "scan.pdf"}
    assert [f["id"] for f in listing["archive"]] == [first["id"]]
    assert next(f for f in listing["files"] if f["file_name"] == "scan.pdf")[
        "extraction_status"] == "none"

    dl = client.get(f"/cv/files/{first['id']}/download")  # архивную версию тоже можно скачать
    assert dl.content == doc

    assert client.delete(f"/cv/files/{second['file']['id']}", headers=headers).status_code == 409
    restored = client.post(f"/cv/files/{first['id']}/restore", headers=headers).json()
    assert [f["id"] for f in restored["archive"]] == [second["file"]["id"]]
    assert client.get(f"/cv/employees/{E1}/main-cv").json()["file"]["id"] == first["id"]
    # сотрудник без файлов
    assert client.get(f"/cv/employees/{E2}/main-cv").json()["extraction_status"] == "none"
    # массовый список для раздела «Резюме» не содержит архива
    bulk = [f for f in client.get("/cv/files").json()["items"] if f["employee_key"] == E1]
    assert all(not f["is_archived"] for f in bulk) and len(bulk) == 3


def test_connection_is_cv_role_only(env):
    """NFR-007: из cv_db таблицы токенов и настроек недоступны и не существуют."""
    _, _, cv_pool, _ = env
    with cv_pool.connection() as c:
        tables = {r[0] for r in c.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')").fetchall()}
        assert not any(t.startswith("app.") or "secret" in t or "app_setting" in t
                       for t in tables)
        assert c.execute("SELECT current_user").fetchone()[0] == "cv_rw"
