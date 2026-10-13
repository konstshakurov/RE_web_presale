"""KON-47 (Pre.QA-2), Q47-08/09/12: файлы сотрудников и CV, извлечение текста и навыков, фильтры
«Резюме», профиль без LLM.

Требования: FR-022, FR-039, FR-040, FR-027, FR-004, FR-003, FR-032, FR-037, BR-007, UC-003, NFR-004.
Файлы генерируются в памяти (вымышленные), после тестов удаляются из cv_db стенда.
"""
from __future__ import annotations

import hashlib

import pytest

from ._fakes import PDF_MIN, PNG_1X1, JiraCtl, LlmCtl, make_docx, requires_fakes, wait_until
from ._stage1 import cleanup_files, cleanup_llm, ensure_allow_list, prepare_cache
from ._stand import Session, logged_in, requires_stand

pytestmark = [requires_stand, requires_fakes]

EMP = {"main": "ANALYTICS-5002", "arch": "ANALYTICS-5003", "extra": "ANALYTICS-5004",
       "skills": "ANALYTICS-5006", "prof": "ANALYTICS-5007", "err": "ANALYTICS-5011"}
ALL_EMP = list(EMP.values())

CV_TEXT = ("Профиль", "Системный аналитик, опыт работы 7 лет. Банковские интеграции.",
           "Основной стек", "SQL, BPMN, UML, REST, Kafka, Jira",
           "Проекты", "Платёжный шлюз: проектирование REST API, SQL-витрины.",
           "Образование", "Высшее техническое")


@pytest.fixture(scope="module")
def admin():
    a = ensure_allow_list()
    yield a
    a.close()


@pytest.fixture(scope="module")
def user(admin):
    JiraCtl().reset()
    s = logged_in("qa-user")
    prepare_cache(s)
    cleanup_files(ALL_EMP)
    LlmCtl().reset()
    yield s
    cleanup_files(ALL_EMP)
    cleanup_llm()
    s.close()


@pytest.fixture(autouse=True)
def _llm_ok():
    LlmCtl().reset()
    yield


def upload(s: Session, key: str, kind: str, name: str, content: bytes, **kw):
    return s.http.post(f"/cv/employees/{key}/files", params={"kind": kind, "file_name": name},
                       content=content, headers={**s.csrf(), "Content-Type": "application/octet-stream"}, **kw)


# --- FR-022: допустимость, лимит, тип файла -----------------------------------------------------

def test_fr022_limits_and_formats_are_exposed(user):
    lim = user.http.get("/cv/limits").json()
    assert lim["max_file_mb"] == 10 and lim["max_file_mb_ceiling"] == 10
    assert lim["cv_extensions"] == ["doc", "docx"]
    assert {"pdf", "png", "jpg", "jpeg"} <= set(lim["materials_extensions"])
    assert set(lim["upload_kinds"]) == {"main_cv", "extra_cv", "materials"}


def test_fr022_main_cv_docx_stored_text_extracted_download_is_identical(user):
    """UC-003 / FR-022 / FR-027 / FR-040: загрузка docx, статус извлечения ok, скачивание == исходному (SHA)."""
    raw = make_docx(*CV_TEXT)
    r = upload(user, EMP["main"], "main_cv", "cv_main_ok.docx", raw)
    assert r.status_code == 201, r.text
    f = r.json()["file"]
    assert f["extraction_status"] == "ok" and f["kind"] == "main_cv" and f["size_bytes"] == len(raw)
    assert f["can_delete"] is False and f["uploaded_by"] == "qa-user"
    dl = user.http.get(f"/cv/files/{f['id']}/download")
    assert dl.status_code == 200
    assert hashlib.sha256(dl.content).hexdigest() == hashlib.sha256(raw).hexdigest()
    assert "attachment" in dl.headers["content-disposition"] and "cv_main_ok.docx" in dl.headers["content-disposition"]
    text = user.http.get(f"/cv/employees/{EMP['main']}/main-cv").json()
    assert text["extraction_status"] == "ok" and "Системный аналитик" in text["text"]
    ft = user.http.get(f"/cv/files/{f['id']}/text").json()
    assert ft["text"] == text["text"]


def test_fr022_materials_pdf_png_txt_accepted_without_extraction(user):
    for name, body in (("works.pdf", PDF_MIN), ("shot.png", PNG_1X1), ("notes.txt", b"plain notes")):
        r = upload(user, EMP["main"], "materials", name, body)
        assert r.status_code == 201, (name, r.text)
        assert r.json()["file"]["extraction_status"] == "none"
        assert r.json()["file"]["kind_title"] == "Материалы"


@pytest.mark.parametrize("kind,name,body,code", [
    ("main_cv", "cv.pdf", PDF_MIN, 415),            # PDF как CV не принимается (решение владельца)
    ("main_cv", "cv.txt", b"text", 415),
    ("extra_cv", "cv.png", PNG_1X1, 415),
    ("materials", "tool.exe", b"MZ\x90\x00", 415),
    ("materials", "noext", b"abc", 415),
    ("main_cv", "fake.docx", PNG_1X1, 415),           # содержимое не соответствует расширению
    ("main_cv", "empty.docx", b"", 422),
])
def test_fr022_invalid_files_rejected_and_not_in_card(user, kind, name, body, code):
    before = len(user.http.get(f"/cv/employees/{EMP['main']}/files").json()["files"])
    r = upload(user, EMP["main"], kind, name, body)
    assert r.status_code == code, r.text
    after = user.http.get(f"/cv/employees/{EMP['main']}/files").json()["files"]
    assert len(after) == before and all(x["file_name"] != name for x in after)


def test_fr022_size_limit_413_and_admin_can_lower_it(user, admin):
    big = b"PK\x03\x04" + b"0" * (10 * 1024 * 1024)             # 10 МБ + 4 байта
    r = upload(user, EMP["main"], "materials", "big.docx", big)
    assert r.status_code == 413, r.status_code
    assert "лимита" in r.json()["detail"]
    # админ снижает лимит до 1 МБ: файл 1.5 МБ отклоняется, 0.5 МБ принимается
    assert admin.http.put("/cv/limits", json={"max_file_mb": 1}, headers=admin.csrf()).status_code == 200
    try:
        assert upload(user, EMP["main"], "materials", "m15.docx",
                      b"PK\x03\x04" + b"1" * 1_500_000).status_code == 413
        assert upload(user, EMP["main"], "materials", "m05.docx",
                      b"PK\x03\x04" + b"1" * 500_000).status_code == 201
        assert user.http.put("/cv/limits", json={"max_file_mb": 5}, headers=user.csrf()).status_code == 403
    finally:
        assert admin.http.put("/cv/limits", json={"max_file_mb": 10}, headers=admin.csrf()).status_code == 200
    assert admin.http.put("/cv/limits", json={"max_file_mb": 11}, headers=admin.csrf()).status_code == 422
    assert admin.http.put("/cv/limits", json={"max_file_mb": 0}, headers=admin.csrf()).status_code == 422


def test_fr022_kind_is_required_and_client_template_not_uploadable(user):
    raw = make_docx("x")
    r = user.http.post(f"/cv/employees/{EMP['main']}/files", params={"file_name": "a.docx"},
                       content=raw, headers=user.csrf())
    assert r.status_code == 422
    r = user.http.post(f"/cv/employees/{EMP['main']}/files",
                       params={"kind": "client_template_cv", "file_name": "a.docx"},
                       content=raw, headers=user.csrf())
    assert r.status_code == 422


def test_fr022_unknown_employee_and_csrf_and_session(user):
    raw = make_docx("x")
    assert upload(user, "ANALYTICS-99999", "materials", "a.docx", raw).status_code == 404
    r = user.http.post(f"/cv/employees/{EMP['main']}/files", params={"kind": "materials", "file_name": "a.docx"},
                       content=raw)                                     # без CSRF-токена
    assert r.status_code == 403
    anon = Session()
    assert anon.http.get("/cv/files").status_code == 401
    assert anon.http.get(f"/cv/files/1/download").status_code == 401


# --- FR-027: сбои извлечения не отменяют загрузку ----------------------------------------------

def test_fr027_corrupt_docx_saved_with_error_status(user):
    r = upload(user, EMP["err"], "main_cv", "cv_corrupt.docx", b"PK\x03\x04" + b"garbage" * 50)
    assert r.status_code == 201, r.text
    f = r.json()["file"]
    assert f["extraction_status"] == "error" and f["extraction_message"]
    card = user.http.get(f"/cv/employees/{EMP['err']}/files").json()["files"]
    assert any(x["id"] == f["id"] for x in card)                          # карточка сохранена
    t = user.http.get(f"/cv/employees/{EMP['err']}/main-cv").json()
    assert t["extraction_status"] == "error" and t["text"] is None


def test_fr027_empty_docx_and_legacy_rtf_doc_report_error(user):
    empty = make_docx("")
    r = upload(user, EMP["err"], "extra_cv", "cv_empty.docx", empty)
    assert r.status_code == 201 and r.json()["file"]["extraction_status"] == "error"
    assert "текст" in r.json()["file"]["extraction_message"].lower()
    rtf = upload(user, EMP["err"], "extra_cv", "cv_legacy.doc", b"{\\rtf1\\ansi Hello}")
    assert rtf.status_code == 201 and rtf.json()["file"]["extraction_status"] == "error"
    assert "DOCX" in rtf.json()["file"]["extraction_message"]


# --- FR-039 / FR-040 / FR-004: версии, архив, восстановление, удаление -------------------------

def test_fr039_main_cv_replacement_archive_of_three_and_restore(user):
    key = EMP["arch"]
    ids = []
    for v in range(1, 6):
        r = upload(user, key, "main_cv", f"cv_v{v}.docx", make_docx(f"Версия {v}", "SQL"))
        assert r.status_code == 201, r.text
        ids.append(r.json()["file"]["id"])
        if v > 1:
            assert r.json()["replaced_file_id"] == ids[-2]
    card = user.http.get(f"/cv/employees/{key}/files").json()
    mains = [f for f in card["files"] if f["kind"] == "main_cv"]
    assert [m["file_name"] for m in mains] == ["cv_v5.docx"]               # один основной
    # FR-039: архив хранит 3 предыдущие версии; самая старая (v1) вытеснена
    assert [a["file_name"] for a in card["archive"]] == ["cv_v4.docx", "cv_v3.docx", "cv_v2.docx"]
    assert all(a["can_restore"] and a["is_archived"] for a in card["archive"])
    assert user.http.get(f"/cv/files/{ids[0]}/download").status_code == 404
    # скачивание архивной версии
    assert user.http.get(f"/cv/files/{ids[2]}/download").status_code == 200
    # восстановление v3: становится основной; прежняя v5 уходит в архив, всего 3
    r = user.http.post(f"/cv/files/{ids[2]}/restore", headers=user.csrf())
    assert r.status_code == 200, r.text
    card = r.json()
    assert [f["file_name"] for f in card["files"] if f["kind"] == "main_cv"] == ["cv_v3.docx"]
    assert len(card["archive"]) == 3 and "cv_v5.docx" in {a["file_name"] for a in card["archive"]}
    # неархивную версию восстановить нельзя
    cur = next(f for f in card["files"] if f["kind"] == "main_cv")
    assert user.http.post(f"/cv/files/{cur['id']}/restore", headers=user.csrf()).status_code == 409
    assert user.http.post("/cv/files/999999999/restore", headers=user.csrf()).status_code == 404


def test_fr040_delete_rules(user):
    key = EMP["arch"]
    card = user.http.get(f"/cv/employees/{key}/files").json()
    cur = next(f for f in card["files"] if f["kind"] == "main_cv")
    assert user.http.delete(f"/cv/files/{cur['id']}", headers=user.csrf()).status_code == 409   # основное не удаляется
    arch = card["archive"][0]
    assert user.http.delete(f"/cv/files/{arch['id']}", headers=user.csrf()).status_code == 204
    assert user.http.get(f"/cv/files/{arch['id']}/download").status_code == 404
    assert user.http.delete(f"/cv/files/{arch['id']}", headers=user.csrf()).status_code == 404
    assert user.http.delete(f"/cv/files/{cur['id']}").status_code == 403                      # без CSRF
    mat = upload(user, key, "materials", "to_delete.pdf", PDF_MIN).json()["file"]
    assert mat["can_delete"] is True
    assert user.http.delete(f"/cv/files/{mat['id']}", headers=user.csrf()).status_code == 204


def test_fr004_several_extra_cv_one_main(user):
    key = EMP["extra"]
    for i in range(3):
        assert upload(user, key, "extra_cv", f"extra_{i}.docx", make_docx(f"Доп. {i}", "SQL")).status_code == 201
    assert upload(user, key, "main_cv", "main_a.docx", make_docx("A", "SQL")).status_code == 201
    r = upload(user, key, "main_cv", "main_b.docx", make_docx("B", "SQL"))
    assert r.status_code == 201 and r.json()["replaced_file_id"] is not None
    card = user.http.get(f"/cv/employees/{key}/files").json()
    assert sum(1 for f in card["files"] if f["kind"] == "extra_cv") == 3
    assert sum(1 for f in card["files"] if f["kind"] == "main_cv") == 1
    # общий список «Файлы» раздела «Резюме»
    allf = user.http.get("/cv/files").json()["items"]
    assert {f["employee_key"] for f in allf if f["file_name"].startswith("extra_")} == {key}


# --- FR-027 / FR-032 / FR-003: навыки, фильтры, профиль ------------------------------------------

def _resume_row(s: Session, key: str, **params) -> dict | None:
    r = s.http.get("/resume/employees", params={"q": key, "limit": 50, **params}).json()
    return next((i for i in r["items"] if i["employee_key"] == key), None)


def test_fr027_skills_extracted_from_main_cv_by_llm_gateway(user):
    llm = LlmCtl()
    key = EMP["skills"]
    r = upload(user, key, "main_cv", "cv_skills.docx", make_docx(*CV_TEXT))
    assert r.status_code == 201
    row = wait_until(lambda: (lambda x: x if x and x["skills_status"] == "ok" else None)(_resume_row(user, key)),
                     timeout=60, interval=2, what="навыки основного CV")
    assert {"sql", "bpmn", "uml"} <= set(row["skills"])
    assert row["extraction_status"] == "ok" and row["main_cv_file_name"] == "cv_skills.docx"
    sent = [c for c in llm.captured() if c["kind"] == "skills"]
    assert sent and "Основной стек" in sent[-1]["prompt"]                  # в LLM ушёл текст CV
    assert not any(key in c["prompt"] for c in sent)                       # ключ сотрудника в запрос не идёт


def test_fr032_resume_filters_without_llm(user):
    llm = LlmCtl()
    key = EMP["skills"]
    wait_until(lambda: _resume_row(user, key) and _resume_row(user, key)["skills_status"] == "ok",
               timeout=60, what="навыки")
    llm.reset()
    r = user.http.get("/resume/employees", params={"technology": "sql", "limit": 200}).json()
    assert key in {i["employee_key"] for i in r["items"]}
    both = user.http.get("/resume/employees", params={"technology": ["sql", "kafka"], "match": "all",
                                                       "limit": 200}).json()
    assert key in {i["employee_key"] for i in both["items"]}
    none = user.http.get("/resume/employees", params={"technology": ["sql", "cobol"], "match": "all"}).json()
    assert key not in {i["employee_key"] for i in none["items"]}
    anyk = user.http.get("/resume/employees", params={"technology": ["cobol", "sql"], "match": "any",
                                                       "limit": 200}).json()
    assert key in {i["employee_key"] for i in anyk["items"]}
    st = _resume_row(user, key)["status"]
    assert key in {i["employee_key"] for i in user.http.get(
        "/resume/employees", params={"status": st, "limit": 200}).json()["items"]}
    txt = user.http.get("/resume/employees", params={"q": "платёжный шлюз", "limit": 200}).json()
    assert key in {i["employee_key"] for i in txt["items"]}               # поиск по тексту CV
    tech = {t["tag"]: t["employees"] for t in user.http.get("/resume/technologies").json()}
    assert tech.get("sql", 0) >= 1
    assert llm.stats()["requests"] == 0                                   # фильтры идут без LLM


def test_fr003_fr037_profile_sections_without_llm(user):
    llm = LlmCtl()
    key = EMP["prof"]
    r = upload(user, key, "main_cv", "cv_profile.docx", make_docx(*CV_TEXT))
    assert r.status_code == 201
    wait_until(lambda: (_resume_row(user, key) or {}).get("skills_status") == "ok", timeout=60,
               what="навыки профиля")
    llm.reset()
    p = user.http.get(f"/resume/employees/{key}/profile")
    assert p.status_code == 200, p.text
    prof = p.json()
    assert prof["main_cv"]["file_name"] == "cv_profile.docx"
    assert set(prof["sections"]) >= {"profile", "stack", "projects", "education"}
    assert "SQL" in prof["sections"]["stack"]
    assert prof["experience_years"] == 7
    assert prof["status"] and prof["description"] and prof["full_name"]
    assert llm.stats()["requests"] == 0                                   # профиль строится без обращения к LLM
    assert user.http.get("/resume/employees/ANALYTICS-99999/profile").status_code == 404
    assert user.http.get("/resume/employees/bad-key/profile").status_code == 422


def test_fr027_replacement_triggers_new_skills_extraction(user):
    """Замена основного CV: навыки пересчитываются по новому файлу (старый набор не остаётся)."""
    key = EMP["skills"]
    upload(user, key, "main_cv", "cv_skills_v2.docx",
           make_docx("Профиль", "Python, Oracle, Camunda", "Основной стек", "Python, Oracle, Camunda"))
    row = wait_until(lambda: (lambda x: x if x and x["skills_status"] == "ok" and "oracle" in x["skills"]
                              else None)(_resume_row(user, key)),
                     timeout=60, interval=2, what="навыки новой версии")
    assert "oracle" in row["skills"] and "bpmn" not in row["skills"]
