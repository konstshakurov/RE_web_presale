"""Вставка контента в docx-шаблон клиента (FR-017, KON-80): документ = шаблон.

LLM возвращает контент блоками (заголовки/абзацы/таблица навыков), а движок встраивает их
в разметку шаблона: заменяет текст внутри существующих параграфов (сохраняя шрифт, цвет,
стиль заголовков из первого run'а), заполняет таблицу навыков клонированием строк-шаблонов.
Колонтитулы (header/footer), стили и темы не трогаются - они физически остаются в пакете.
Только стандартная библиотека (xml.etree + zipfile).
"""
from __future__ import annotations

import re
import zipfile
from io import BytesIO
from typing import Sequence
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W}
Document = ET.Element

_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


class TemplateError(Exception):
    """Шаблон не читается как docx (сообщение безопасно показывать)."""


def _w(tag: str) -> str:
    return f"{{{_W}}}{tag}"


def _norm(s: str) -> str:
    return re.sub(r"[\s:—–-]+", " ", (s or "").lower()).strip()


def _para_text(p: Document) -> str:
    return "".join(t.text or "" for t in p.iter(_w("t")))


def _clear_runs_keep_style(p: Document, text: str) -> None:
    """Заменить текст параграфа, сохранив форматирование первого run (rPr)."""
    runs = [r for r in p.iter() if r.tag == _w("r")]
    if not runs:
        r = ET.SubElement(p, _w("r"))
        t = ET.SubElement(r, _w("t"))
        t.text = text
        return
    first = runs[0]
    ft = first.find(_w("t"))
    if ft is None:
        ft = ET.SubElement(first, _w("t"))
    ft.text = text
    ft.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for parent in p.iter():
        for r in list(parent):
            if r.tag == _w("r") and r is not first:
                parent.remove(r)


def _clone(tbl: Document, rows: Sequence[Sequence[str]]) -> None:
    """Заполнить таблицу: строки данных клонируются из последней строки-шаблона."""
    trs = tbl.findall(_w("tr"))
    if not trs:
        return
    header, template_row = trs[0], trs[-1]
    for tr in trs[1:]:
        tbl.remove(tr)
    for values in rows:
        new_tr = ET.fromstring(ET.tostring(template_row))
        tcs = new_tr.findall(_w("tc"))
        for i, tc in enumerate(tcs):
            value = values[i] if i < len(values) else ""
            ps = tc.findall(_w("p"))
            target = ps[0] if ps else ET.SubElement(tc, _w("p"))
            _clear_runs_keep_style(target, str(value))
        tbl.append(new_tr)


def _match_score(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def apply_blocks_to_template(template_bytes: bytes, blocks: Sequence[dict]) -> bytes:
    """Собрать docx: файл шаблона, в который вставлен контент блоков.

    Блоки: {"type": "heading"|"para", "text": str} | {"type": "skills", "rows": [[..],..]}.
    Заголовок сопоставляется с существующим параграфом шаблона (нечётко); тексты заменяются
    с сохранением формата. Таблица навыков заполняется по заголовку «навыки/стек».
    """
    try:
        zin = zipfile.ZipFile(BytesIO(template_bytes))
    except zipfile.BadZipFile as exc:
        raise TemplateError("Файл шаблона не читается как docx") from exc
    parts = {i.filename: zin.read(i.filename) for i in zin.infolist()}
    doc_name = "word/document.xml"
    if doc_name not in parts:
        raise TemplateError("В шаблоне нет word/document.xml")
    try:
        root = ET.fromstring(parts[doc_name])
    except ET.ParseError as exc:
        raise TemplateError("Разметка шаблона повреждена") from exc

    body = root.find(_w("body"))
    if body is None:
        raise TemplateError("В шаблоне нет тела документа")

    children = list(body)
    paras = [el for el in children if el.tag == _w("p")]
    tables = [el for el in children if el.tag == _w("tbl")]

    def insert_after(anchor: Document, el: Document) -> None:
        body.insert(list(body).index(anchor) + 1, el)

    def find_heading(text: str, start_idx: int = 0):
        best, best_score = None, 0.55
        for idx, p in enumerate(paras):
            if idx < start_idx:
                continue
            score = _match_score(_para_text(p), text)
            if score > best_score:
                best, best_score = (idx, p), score
        return best

    def table_after(idx: int):
        for el in children[idx + 1 if idx is not None else 0:]:
            if el.tag == _w("tbl"):
                return el
        return None

    cursor = 0  # индекс по paras
    WNS = "xmlns:w=" + chr(34) + _W + chr(34)

    def new_para(text: str) -> Document:
        return ET.fromstring(
            f"<w:p {WNS}><w:r><w:t xml:space=" + chr(34) + "preserve" + chr(34) + ">"
            f"{escape(text)}</w:t></w:r></w:p>")

    for block in blocks:
        btype = block.get("type")
        if btype == "heading":
            found = find_heading(block.get("text", ""), cursor)
            if found:
                cursor = found[0] + 1
                _clear_runs_keep_style(found[1], block.get("text", ""))
            else:
                anchor = paras[cursor - 1] if cursor else paras[-1] if paras else body[-1]
                insert_after(anchor, new_para(block.get("text", "")))
        elif btype == "para":
            # абзац заменяет ближайший параграф-заполнитель после текущего заголовка;
            # если следующий параграф - заголовок нового раздела, контент вставляется перед ним
            target = paras[cursor] if cursor < len(paras) else None
            if target is not None:
                ppr = target.find(_w("pPr"))
                is_heading = (ppr is not None and
                              ppr.find(_w("pStyle")) is not None)
                if is_heading:
                    body.insert(list(body).index(target), new_para(block.get("text", "")))
                else:
                    _clear_runs_keep_style(target, block.get("text", ""))
                    cursor += 1
            else:
                anchor = paras[-1] if paras else body[0]
                insert_after(anchor, new_para(block.get("text", "")))
        elif btype == "skills":
            heading = find_heading("ключевые навыки", max(0, cursor - 3))
            if heading is None:
                heading = find_heading("навыки", max(0, cursor - 3))
            tbl = table_after(heading[0]) if heading else (tables[0] if tables else None)
            if tbl is not None:
                _clone(tbl, block.get("rows") or [])

    parts[doc_name] = (_DECL + ET.tostring(root, encoding="unicode")).encode("utf-8")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in parts.items():
            zout.writestr(name, data)
    return buf.getvalue()
