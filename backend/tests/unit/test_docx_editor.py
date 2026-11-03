"""Вставка контента в docx-шаблон (KON-80): стили, шрифт, колонтитулы и таблицы сохраняются.

Шаблоны и данные - синтетические (без персональных данных).
"""
from __future__ import annotations

import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

import pytest

from app.cvtpl.docx_editor import TemplateError, apply_blocks_to_template

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def template_docx(header: str = "RE_WEB_PRESALE (KON-49)") -> bytes:
    """Шаблон: заголовок со стилем Heading1, параграф, таблица навыков 2 строки, колонтитул."""
    CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
          "</Types>")
    RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="word/header1.xml"/>'
            "</Relationships>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:rPr><w:b/></w:rPr>'
        '<w:t>Опыт работы</w:t></w:r></w:p>'
        '<w:p><w:r><w:rPr><w:sz w:val="22"/></w:rPr><w:t>Компания: место для замены</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Ключевые навыки (Основной стек)</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Навык</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>Уровень</w:t></w:r></w:p></w:tc></w:tr>'
        '<w:tr><w:tc><w:p><w:r><w:t>пример</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>лет</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        "</w:body></w:document>")
    header1 = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<w:hdr xmlns:w="{_W}"><w:p><w:r><w:t>{header}</w:t></w:r></w:p></w:hdr>')
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/header1.xml", header1)
    return buf.getvalue()


def _doc_xml(docx: bytes) -> ET.Element:
    with zipfile.ZipFile(BytesIO(docx)) as z:
        return ET.fromstring(z.read("word/document.xml"))


def _texts(docx: bytes, tag: str) -> list[str]:
    root = _doc_xml(docx)
    return ["".join(t.text or "" for t in el.iter("{%s}t" % _W))
            for el in root.iter("{%s}%s" % (_W, tag))]


def test_styles_and_parts_preserved():
    blocks = [
        {"type": "heading", "text": "Опыт работы"},
        {"type": "para", "text": "СимбирСофт, аналитик, 2021-2026"},
        {"type": "skills", "rows": [["SQL", "5 лет"], ["Python", "3 года"], ["BPMN", "2 года"]]},
    ]
    out = apply_blocks_to_template(template_docx(), blocks)
    # документ остался docx со ВСЕМИ частями (колонтитул на месте)
    with zipfile.ZipFile(BytesIO(out)) as z:
        names = z.namelist()
        assert "word/header1.xml" in names
        hdr = z.read("word/header1.xml").decode()
        assert "RE_WEB_PRESALE" in hdr
    # заголовок сохранил стиль Heading1
    root = _doc_xml(out)
    found_h1 = False
    for p in root.iter("{%s}p" % _W):
        ppr = p.find("{%s}pPr" % _W)
        if ppr is None:
            continue
        style = ppr.find("{%s}pStyle" % _W)
        if style is not None and style.get("{%s}val" % _W) == "Heading1":
            found_h1 = True
    assert found_h1
    # контент заменён: «Компания: место для замены» -> СимбирСофт
    paras = _texts(out, "p")
    assert any("СимбирСофт" in t for t in paras)
    assert not any("место для замены" in t for t in paras)


def test_skills_table_rows_grown_and_filled():
    blocks = [{"type": "skills", "rows": [["SQL", "5 лет"], ["Python", "3 года"], ["BPMN", "2 года"]]}]
    out = apply_blocks_to_template(template_docx(), blocks)
    root = _doc_xml(out)
    tbl = next(el for el in root.iter("{%s}tbl" % _W))
    trs = tbl.findall("{%s}tr" % _W)
    assert len(trs) == 4  # шапка + 3 навыка (шаблонная строка была 1)
    texts = "".join(t.text or "" for t in tbl.iter("{%s}t" % _W))
    assert "SQL" in texts and "Python" in texts and "BPMN" in texts and "пример" not in texts


def test_unknown_heading_added_to_end():
    blocks = [{"type": "heading", "text": "Новый раздел"}, {"type": "para", "text": "Текст"}]
    out = apply_blocks_to_template(template_docx(), blocks)
    assert any("Новый раздел" in t for t in _texts(out, "p"))
    assert any(t == "Текст" for t in _texts(out, "p"))


def test_not_docx_raises():
    with pytest.raises(TemplateError):
        apply_blocks_to_template(b"not a zip", [])