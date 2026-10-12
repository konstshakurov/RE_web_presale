"""Извлечение текста из CV при загрузке (FR-027): DOCX, DOC (Word 97-2003); других форматов CV нет.

Только стандартная библиотека. Формат определяется по содержимому, а не по расширению.
Навыки из текста выделяет LLM (KON-38), здесь только текст.
"""
from __future__ import annotations

import html
import re
import struct
import zipfile
from io import BytesIO

from .formats import EXTRACTABLE, extension

OK, ERROR, NONE = "ok", "error", "none"

_CFB_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_FREE = 0xFFFFFFFA  # значения от него и выше - служебные (конец цепочки, свободный, FAT, DIFAT)
_MAX_XML = 64 * 1024 * 1024  # распакованный document.xml: защита от zip-бомбы
_MAX_SECTORS = 1 << 20


class ExtractError(Exception):
    """Текст извлечь не удалось; сообщение безопасно показывать пользователю."""


def extract_text(file_name: str, content: bytes) -> str:
    """Текст файла или ExtractError. Пустой результат - тоже ошибка (скан без текста)."""
    ext = extension(file_name)
    if ext not in EXTRACTABLE:
        raise ExtractError(f"Извлечение текста для формата .{ext or '?'} не поддерживается")
    try:
        if content.startswith(b"PK\x03\x04"):
            text = _docx(content)
        elif content.startswith(_CFB_SIG):
            text = _doc(content)
        elif ext == "doc":
            raise ExtractError("Файл .doc в неподдерживаемом формате (например RTF): "
                               "сохраните как DOCX")
        else:
            raise ExtractError("Файл повреждён: содержимое не похоже на документ Word")
    except ExtractError:
        raise
    except (zipfile.BadZipFile, KeyError, struct.error, IndexError, ValueError, OverflowError):
        raise ExtractError("Файл повреждён или имеет неожиданную структуру") from None
    text = _normalize(text)
    if not text:
        raise ExtractError("В файле не найден текст (возможно, это скан)")
    return text


def _normalize(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v\xa0]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _docx(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        try:
            info = zf.getinfo("word/document.xml")
        except KeyError:
            raise ExtractError("Файл не является документом Word (DOCX)") from None
        if info.file_size > _MAX_XML:
            raise ExtractError("Документ слишком велик для извлечения текста")
        xml = zf.read(info).decode("utf-8", errors="replace")
    # регулярные выражения вместо XML-парсера: внешние сущности не обрабатываются вовсе
    xml = re.sub(r"</w:p>|<w:br\s*/?>|<w:cr\s*/?>", "\n", xml)
    xml = re.sub(r"</w:tc>|<w:tab\s*/?>", "\t", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


# --- DOC: контейнер OLE2 (CFB) + таблица фрагментов Word 97 ---

def _cfb_streams(data: bytes) -> dict[str, bytes]:
    """Потоки контейнера по имени (каталог читается плоско, без дерева)."""
    if len(data) < 512:
        raise ExtractError("Файл повреждён")
    sec_size = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    mini_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
    n_fat = struct.unpack_from("<I", data, 0x2C)[0]
    dir_start = struct.unpack_from("<I", data, 0x30)[0]
    cutoff = struct.unpack_from("<I", data, 0x38)[0]
    minifat_start, n_minifat = struct.unpack_from("<II", data, 0x3C)
    difat_start, n_difat = struct.unpack_from("<II", data, 0x44)
    if sec_size not in (512, 4096):
        raise ExtractError("Файл повреждён")

    def sector(n: int) -> bytes:
        off = (n + 1) * sec_size
        if off >= len(data):
            raise ExtractError("Файл повреждён")
        return data[off:off + sec_size]

    fat_ids = [x for x in struct.unpack_from("<109I", data, 0x4C) if x < _FREE]
    nxt, hops = difat_start, 0
    while nxt < _FREE and hops < n_difat:
        vals = struct.unpack("<%dI" % (sec_size // 4), sector(nxt))
        fat_ids += [x for x in vals[:-1] if x < _FREE]
        nxt, hops = vals[-1], hops + 1
    fat: list[int] = []
    for sid in fat_ids[:n_fat]:
        fat += struct.unpack("<%dI" % (sec_size // 4), sector(sid))

    def chain(start: int) -> bytes:
        out, seen = [], 0
        while start < _FREE:
            if start >= len(fat) or seen > _MAX_SECTORS:
                raise ExtractError("Файл повреждён")
            out.append(sector(start))
            start, seen = fat[start], seen + 1
        return b"".join(out)

    directory = chain(dir_start)
    entries = []
    for i in range(0, len(directory) - 127, 128):
        e = directory[i:i + 128]
        name_len = struct.unpack_from("<H", e, 64)[0]
        name = e[:max(min(name_len, 64) - 2, 0)].decode("utf-16-le", errors="replace")
        start = struct.unpack_from("<I", e, 116)[0]
        size = struct.unpack_from("<I", e, 120)[0]  # версия 3: размер в младших 32 битах
        entries.append((name, e[66], start, size))
    root = next((e for e in entries if e[1] == 5), None)
    mini_stream = chain(root[2]) if root and root[2] < _FREE else b""
    minifat: list[int] = []
    if n_minifat and minifat_start < _FREE:
        raw = chain(minifat_start)
        minifat = list(struct.unpack("<%dI" % (len(raw) // 4), raw[: len(raw) // 4 * 4]))

    def mini_chain(start: int, size: int) -> bytes:
        out, seen = [], 0
        while start < _FREE and seen <= _MAX_SECTORS:
            if start >= len(minifat):
                raise ExtractError("Файл повреждён")
            out.append(mini_stream[start * mini_size:(start + 1) * mini_size])
            start, seen = minifat[start], seen + 1
        return b"".join(out)[:size]

    streams: dict[str, bytes] = {}
    for name, etype, start, size in entries:
        if etype != 2 or name in streams:
            continue
        streams[name] = mini_chain(start, size) if size < cutoff else chain(start)[:size]
    return streams


_FIELD = re.compile(r"\x13[^\x14\x15]*\x14|\x13[^\x15]*\x15|[\x14\x15]")


def _doc(content: bytes) -> str:
    streams = _cfb_streams(content)
    word = streams.get("WordDocument")
    if word is None or len(word) < 0x1A2 + 8:
        raise ExtractError("Файл не является документом Word")
    if struct.unpack_from("<H", word, 0)[0] != 0xA5EC:
        raise ExtractError("Версия документа Word не поддерживается")
    flags = struct.unpack_from("<H", word, 0x0A)[0]
    if flags & 0x0100:
        raise ExtractError("Документ защищён паролем")
    table = streams.get("1Table" if flags & 0x0200 else "0Table")
    ccp_text = struct.unpack_from("<I", word, 0x4C)[0]
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x1A2)
    if table is None or not lcb_clx:
        raise ExtractError("Не найдена таблица текста документа")
    clx = table[fc_clx:fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:  # Prc: пропускаем свойства
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 0x02:
        raise ExtractError("Не найдена таблица фрагментов текста")
    lcb = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5:pos + 5 + lcb]
    n = (lcb - 4) // 12
    cps = struct.unpack_from("<%dI" % (n + 1), plc, 0)
    parts: list[str] = []
    taken = 0
    for i in range(n):
        if taken >= ccp_text:
            break
        count = min(cps[i + 1] - cps[i], ccp_text - taken)
        fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * i + 2)[0]
        if fc & 0x40000000:  # сжатый фрагмент: cp1252, по байту на символ
            start = (fc & 0x3FFFFFFF) // 2
            parts.append(word[start:start + count].decode("cp1252", errors="replace"))
        else:
            start = fc & 0x3FFFFFFF
            parts.append(word[start:start + 2 * count].decode("utf-16-le", errors="replace"))
        taken += count
    text = "".join(parts)
    text = _FIELD.sub("", text).replace("\x07", "\t").replace("\x0b", "\n").replace("\x0c", "\n")
    return re.sub(r"[\x00-\x08\x0e-\x1f]", "", text)
