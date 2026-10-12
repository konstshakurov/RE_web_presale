"""Форматы и имена файлов сотрудников (FR-022). Список форматов в ФТ не назван, решение владельца:
CV (основное и доп.) - только DOC и DOCX (PDF и TXT не принимаются); материалы - DOC, DOCX, PDF,
TXT, XLS, XLSX, PPT, PPTX, PNG, JPG, JPEG (текст из материалов не извлекается).
Единственное место, где перечислены форматы.
"""
from __future__ import annotations

import re
from typing import Literal

#: Типы, которые пользователь выбирает при загрузке. «CV по шаблону клиента» создаёт
#: система (FR-017).
UploadKind = Literal["main_cv", "extra_cv", "materials"]
UPLOAD_KINDS: tuple[str, ...] = ("main_cv", "extra_cv", "materials")
CV_KINDS = frozenset({"main_cv", "extra_cv", "client_template_cv"})

KIND_TITLES = {
    "main_cv": "Основное CV",
    "extra_cv": "Доп. CV",
    "materials": "Материалы",
    "client_template_cv": "CV по шаблону клиента",
}

CV_EXTENSIONS = frozenset({"doc", "docx"})
MATERIAL_EXTENSIONS = frozenset(
    {"doc", "docx", "pdf", "txt", "xls", "xlsx", "ppt", "pptx", "png", "jpg", "jpeg"})
#: Расширения CV, из которых достаём текст (FR-027)
EXTRACTABLE = frozenset({"doc", "docx"})

MIME = {
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
NAME_MAX = 255


def allowed_extensions(kind: str) -> frozenset[str]:
    return MATERIAL_EXTENSIONS if kind == "materials" else CV_EXTENSIONS


def clean_file_name(raw: str) -> str:
    """Имя без пути и управляющих символов, не длиннее 255; пустое - пустая строка."""
    name = _CTRL.sub("", raw.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    if len(name) > NAME_MAX:
        stem, dot, ext = name.rpartition(".")
        name = stem[: NAME_MAX - len(ext) - 1] + dot + ext if dot else name[:NAME_MAX]
    return name


def extension(file_name: str) -> str:
    return file_name.rpartition(".")[2].lower() if "." in file_name else ""


def signature_ok(ext: str, head: bytes) -> bool:
    """Первые байты не противоречат расширению (защита от явного мусора под видом документа)."""
    if ext == "docx":
        return head.startswith(b"PK\x03\x04")
    if ext == "doc":  # бывает DOCX и RTF под расширением .doc
        return head.startswith((b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"PK\x03\x04", b"{\\rtf"))
    if ext == "pdf":
        return head.startswith(b"%PDF") or b"%PDF" in head[:1024]
    return True
