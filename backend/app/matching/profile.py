"""Профиль сотрудника без обращения к внешней LLM (FR-003).

Разделы корпоративного CV (BR-007: профиль, основной стек, отрасли, ключевые навыки, языки,
образование, проекты) вырезаются из извлечённого текста по заголовкам. Если заголовков нет,
разделы пусты, а текст доступен целиком через кнопку «Резюме» (FR-037).
"""
from __future__ import annotations

import re

#: Код раздела -> варианты заголовка (нижний регистр, без двоеточия)
SECTION_TITLES: dict[str, tuple[str, ...]] = {
    "profile": ("профиль", "о себе", "обо мне", "summary", "profile"),
    "stack": ("основной стек", "стек", "технологии", "основной стек технологий", "tech stack"),
    "industries": ("отрасли", "отраслевой опыт", "индустрии", "industries"),
    "skills": ("ключевые навыки", "навыки", "компетенции", "key skills", "skills"),
    "languages": ("языки", "знание языков", "иностранные языки", "languages"),
    "education": ("образование", "education"),
    "projects": ("проекты", "опыт работы", "опыт проектов", "projects", "experience"),
}
MAX_SECTION_CHARS = 4000
_HEADING_MAP = {t: code for code, titles in SECTION_TITLES.items() for t in titles}
_YEARS = re.compile(
    r"(?:опыт[^\n.]{0,40}?|стаж[^\n.]{0,20}?)\b(\d{1,2})\s*(?:\+\s*)?(?:лет|года|год)\b",
    re.IGNORECASE)


def _heading(line: str) -> str | None:
    t = line.strip().rstrip(":").strip().lower()
    return _HEADING_MAP.get(t) if 0 < len(t) <= 40 else None


def parse_sections(text: str | None) -> dict[str, str]:
    """{код раздела: текст}. Повтор заголовка дописывает в тот же раздел."""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in (text or "").splitlines():
        code = _heading(line)
        if code is not None:
            current = code
            out.setdefault(code, [])
        elif current is not None and line.strip():
            out[current].append(line.strip())
    return {c: "\n".join(v)[:MAX_SECTION_CHARS] for c, v in out.items() if v}


def guess_years(text: str | None) -> int | None:
    """Годы опыта, если CV называет их явно («опыт работы 7 лет»); иначе None (не выдумываем)."""
    years = [int(m.group(1)) for m in _YEARS.finditer(text or "")]
    return max(years) if years else None
