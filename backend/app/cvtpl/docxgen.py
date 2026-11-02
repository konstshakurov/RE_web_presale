"""Минимальный DOCX изPlain текста (KON-80): без внешних зависимостей.

Word 2007 откроет такой файл; повторное извлечение текста приложением (cv.extract)
вернёт те же абзацы. Конструкций шаблона нет: только абзацы (qb).
"""
from __future__ import annotations

import zipfile
from io import BytesIO

_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd'
    '.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org"
    "/officeDocument/2006/relationships/officeDocument\" "
    'Target="word/document.xml"/></Relationships>'
)


def _p(line: str) -> str:
    if not line.strip():
        return '<w:p/>'  # пустая строка - разделитель абзацев
    from xml.sax.saxutils import escape

    return (
        '<w:p><w:r><w:t xml:space="preserve">'
        + escape(line)
        + "</w:t></w:r></w:p>"
    )


def build_docx(text: str) -> bytes:
    """DOCX (Word 2007+) с абзацами '' текста; пустые строки - пустые абзацы."""
    body = "".join(_p(line.rstrip()) for line in text.replace("\r\n", "\n").split("\n"))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document)
    return buf.getvalue()
