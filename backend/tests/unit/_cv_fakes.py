"""Общие помощники тестов файлов CV (KON-36): синтетические DOCX/DOC и приложение с подменой пула.

Реальных CV и персональных данных нет: тексты придуманы для теста.
"""
from __future__ import annotations

import base64
import io
import os
import struct
import zipfile
from datetime import UTC, datetime
from html import escape

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app_repo.repo import AppUser
from app.auth.sessions import csrf_token, hash_session_id, new_session_id
from app.config import Secret, Settings
from app.cv.router import router as cv_router

KEY = base64.b64encode(os.urandom(32)).decode()
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def build_docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


def build_cfb(streams: dict[str, bytes]) -> bytes:
    """Минимальный контейнер OLE2 версии 3 (секторы 512, мини-секторы 64, порог 4096)."""
    sec, mini, cutoff = 512, 64, 4096
    ends, free, fat_id = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD
    small = {n: d for n, d in streams.items() if len(d) < cutoff}
    big = {n: d for n, d in streams.items() if len(d) >= cutoff}

    mini_stream = b""
    minifat: list[int] = []
    small_start: dict[str, int] = {}
    for name, data in small.items():
        count = max(1, -(-len(data) // mini))
        first = len(minifat)
        small_start[name] = first
        minifat += [first + i + 1 for i in range(count - 1)] + [ends]
        mini_stream += data.ljust(count * mini, b"\0")

    def nsec(n: int) -> int:
        return max(1, -(-n // sec))

    dir_entries = 1 + len(streams)
    n_dir = nsec(dir_entries * 128)
    n_minifat = nsec(len(minifat) * 4) if minifat else 0
    n_mini_stream = nsec(len(mini_stream)) if mini_stream else 0
    big_secs = {n: nsec(len(d)) for n, d in big.items()}
    payload = n_dir + n_minifat + n_mini_stream + sum(big_secs.values())
    n_fat = 1
    while (payload + n_fat) * 4 > n_fat * sec:
        n_fat += 1
    assert n_fat <= 109

    fat: list[int] = []
    sectors: list[bytes] = []

    def alloc(data: bytes, count: int) -> int:
        first = len(sectors)
        for i in range(count):
            fat.append(first + i + 1 if i < count - 1 else ends)
            sectors.append(data[i * sec:(i + 1) * sec].ljust(sec, b"\0"))
        return first

    start: dict[str, int] = {}
    for name, data in big.items():
        start[name] = alloc(data, big_secs[name])
    mini_start = alloc(mini_stream, n_mini_stream) if mini_stream else ends
    minifat_start = (
        alloc(b"".join(struct.pack("<I", x) for x in minifat), n_minifat) if minifat else ends
    )

    def entry(name: str, etype: int, first: int, size: int, left=free, right=free, child=free):
        raw = name.encode("utf-16-le")
        return (
            raw.ljust(64, b"\0") + struct.pack("<H", len(raw) + 2 if name else 0)
            + bytes([etype, 1]) + struct.pack("<III", left, right, child) + b"\0" * 16
            + struct.pack("<I", 0) + b"\0" * 16 + struct.pack("<I", first) + struct.pack("<Q", size)
        )

    names = list(streams)
    dirs = [entry("Root Entry", 5, mini_start, len(mini_stream), child=1 if names else free)]
    for i, name in enumerate(names):
        first = start[name] if name in big else small_start[name]
        dirs.append(entry(name, 2, first, len(streams[name]),
                          right=i + 2 if i + 1 < len(names) else free))
    dir_start = alloc(b"".join(dirs), n_dir)
    fat_start = len(sectors)
    fat += [fat_id] * n_fat
    fat_bytes = b"".join(struct.pack("<I", x) for x in fat).ljust(n_fat * sec, b"\xff")
    for i in range(n_fat):
        sectors.append(fat_bytes[i * sec:(i + 1) * sec])

    difat = [fat_start + i for i in range(n_fat)] + [free] * (109 - n_fat)
    header = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 16 + struct.pack("<HHH", 0x3E, 3, 0xFFFE)
        + struct.pack("<HH", 9, 6) + b"\0" * 6 + struct.pack("<I", 0)  # секторов каталога: 0 (v3)
        + struct.pack("<I", n_fat) + struct.pack("<I", dir_start) + struct.pack("<I", 0)
        + struct.pack("<I", cutoff) + struct.pack("<I", minifat_start)
        + struct.pack("<I", n_minifat)
        + struct.pack("<I", ends) + struct.pack("<I", 0)
        + b"".join(struct.pack("<I", x) for x in difat)
    )
    return header.ljust(sec, b"\0") + b"".join(sectors)


def build_doc(
    text: str, *, unicode: bool = True, pad: bool = False, encrypted: bool = False
) -> bytes:
    """DOC Word 97: одна запись таблицы фрагментов; ``pad`` - потоки >= 4096 (обычные секторы)."""
    raw = text.encode("utf-16-le") if unicode else text.encode("cp1252")
    offset = 0x400
    word = bytearray(offset) + raw
    struct.pack_into("<H", word, 0, 0xA5EC)
    struct.pack_into("<H", word, 0x0A, 0x0200 | (0x0100 if encrypted else 0))
    struct.pack_into("<I", word, 0x4C, len(text))
    fc = offset if unicode else (offset * 2) | 0x40000000
    plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, fc, 0)
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<II", word, 0x1A2, 0, len(clx))
    table = bytes(clx)
    if pad:
        word += b"\0" * (4096 - len(word)) if len(word) < 4096 else b""
        table += b"\0" * (4096 - len(table))
    return build_cfb({"WordDocument": bytes(word), "1Table": table})


class AuthRepo:
    def __init__(self, role: str = "user") -> None:
        self.sessions: dict[bytes, str] = {}
        self.role = role

    def get_user(self, login):
        return AppUser(login, self.role, True)

    def get_active_session(self, h):
        return (self.sessions[h], T0) if h in self.sessions else None

    def touch_session(self, h): ...

    def revoke_session(self, h): ...


def make_client(cv_repo, limits, role: str = "user"):
    """TestClient c роутером cv; возвращает (client, csrf_headers, app)."""
    app = FastAPI()
    app.include_router(cv_router)
    auth = AuthRepo(role)
    app.state.settings = Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(KEY))
    app.state.repo = auth
    app.state.cv = cv_repo
    app.state.cv_limits = limits
    sid = new_session_id()
    auth.sessions[hash_session_id(sid)] = "ivan"
    client = TestClient(app)
    client.cookies.set("re_session", sid)
    headers = {"X-CSRF-Token": csrf_token(app.state.settings.enc_key_bytes(), sid)}
    return client, headers, app
