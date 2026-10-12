"""Извлечение текста из CV (FR-027): DOCX, DOC, отказы. БД не нужна."""
from __future__ import annotations

import pytest

from app.cv.extract import ExtractError, extract_text
from app.cv.formats import clean_file_name, extension, signature_ok

from ._cv_fakes import build_cfb, build_doc, build_docx

TEXT = "Старший аналитик\nОпыт: BABOK, SQL, REST API\n\nПроекты: учебный стенд"


def test_docx_paragraphs():
    out = extract_text("cv.docx", build_docx(["Старший аналитик", "Навыки: SQL & UML <b>"]))
    assert out == "Старший аналитик\nНавыки: SQL & UML <b>"


def test_docx_detected_by_content_even_with_doc_extension():
    assert "SQL" in extract_text("cv.doc", build_docx(["SQL"]))


@pytest.mark.parametrize("pad", [False, True], ids=["mini-stream", "regular-sectors"])
@pytest.mark.parametrize("unicode", [True, False], ids=["utf16", "cp1252"])
def test_doc_pieces(pad, unicode):
    text = TEXT if unicode else "Senior analyst\rSkills: SQL, UML\r"
    out = extract_text("cv.doc", build_doc(text, unicode=unicode, pad=pad))
    assert out == text.replace("\r", "\n").strip()


def test_doc_fields_and_cell_marks():
    text = "Итого\x13 HYPERLINK \"x\"\x14ссылка\x15 текст\x07ячейка\r"
    assert extract_text("cv.doc", build_doc(text)) == "Итогоссылка текст\tячейка"


def test_doc_password_protected():
    with pytest.raises(ExtractError, match="паролем"):
        extract_text("cv.doc", build_doc("x", encrypted=True))


def test_doc_not_word_container():
    with pytest.raises(ExtractError):
        extract_text("cv.doc", build_cfb({"Other": b"abc"}))


def test_corrupted_inputs_give_extract_error_not_crash():
    cfb = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600
    cases = [("a.docx", b"PK\x03\x04garbage"), ("a.doc", cfb),
             ("a.doc", b"{\\rtf1 hello}"), ("a.docx", b"not a zip")]
    for name, data in cases:
        with pytest.raises(ExtractError):
            extract_text(name, data)


def test_empty_text_is_error():
    with pytest.raises(ExtractError, match="не найден текст"):
        extract_text("scan.docx", build_docx(["  "]))


@pytest.mark.parametrize("name", ["a.txt", "a.pdf"])
def test_txt_pdf_not_extractable(name):
    with pytest.raises(ExtractError, match="не поддерживается"):
        extract_text(name, b"%PDF-1.4")


def test_format_lists():
    from app.cv.formats import CV_EXTENSIONS, EXTRACTABLE, MATERIAL_EXTENSIONS
    assert CV_EXTENSIONS == {"doc", "docx"} == EXTRACTABLE
    assert {"pdf", "txt", "xls", "xlsx", "ppt", "pptx", "png", "jpg", "jpeg"} <= MATERIAL_EXTENSIONS


def test_file_name_helpers():
    assert clean_file_name("C:\\tmp\\..\\CV Резюме.docx") == "CV Резюме.docx"
    assert clean_file_name("a\x00b\n.docx") == "ab.docx"
    long = clean_file_name("x" * 400 + ".docx")
    assert len(long) == 255 and long.endswith(".docx")
    assert extension("A.B.DOCX") == "docx" and extension("noext") == ""


def test_signatures():
    assert signature_ok("docx", b"PK\x03\x04..") and not signature_ok("docx", b"hello")
    assert signature_ok("doc", b"{\\rtf1") and not signature_ok("doc", b"hello")
    assert signature_ok("pdf", b"%PDF-1.7") and not signature_ok("pdf", b"hello")
    assert signature_ok("png", b"anything")
