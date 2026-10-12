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
    "education": ("образование", "образование и сертификаты", "образование и курсы",
                  "сертификаты", "education"),
    "projects": ("проекты", "опыт работы", "опыт проектов", "projects", "experience"),
}
MAX_SECTION_CHARS = 4000
#: Раздел проектов в корпоративном CV самый большой: режем по более высокому пределу
SECTION_LIMITS = {"projects": 12000}
#: Короткие слова, которые внутри описания проекта встречаются отдельной строкой («Стек»):
#: после начала раздела проектов раздел они не переключают
_AMBIGUOUS = {"стек", "технологии", "навыки", "компетенции", "skills", "profile", "summary"}
_HEADING_MAP = {t: code for code, titles in SECTION_TITLES.items() for t in titles}
_NUM = r"(\d{1,2})\s*(?:\+\s*)?"
_UNIT = r"(?:лет|года|год|годами|годам)\b"
_YEARS = re.compile(
    # «опыт работы 7 лет», «стаж 5 лет»
    rf"(?:опыт[^\n.]{{0,40}}?|стаж[^\n.]{{0,20}}?)\b{_NUM}{_UNIT}"
    # «более 5 лет опыта», «более чем 7 годами опыта», «7+ лет практического опыта»
    rf"|\b{_NUM}{_UNIT}\s+(?:\w+\s+)?(?:опыта|опытом|стажа)\b",
    re.IGNORECASE)
_BULLET = re.compile(r"^(?:\d+[.)]\s+|[-•*–—]\s+)")


def _title(line: str) -> str:
    t = _BULLET.sub("", line.strip()).rstrip(":").strip().lower()
    return re.sub(r"\s+", " ", t)


def _heading(line: str) -> str | None:
    t = _title(line)
    return _HEADING_MAP.get(t) if 0 < len(t) <= 40 else None


def parse_sections(text: str | None) -> dict[str, str]:
    """{код раздела: текст}. Повтор заголовка дописывает в тот же раздел."""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in (text or "").splitlines():
        code = _heading(line)
        if code is not None and current == "projects" and _title(line) in _AMBIGUOUS:
            code = None  # «Стек» внутри описания проекта - не новый раздел
        if code is not None:
            current = code
            out.setdefault(code, [])
        elif current is not None and line.strip():
            out[current].append(line.strip())
    return {c: "\n".join(v)[:SECTION_LIMITS.get(c, MAX_SECTION_CHARS)]
            for c, v in out.items() if v}


def guess_years(text: str | None) -> int | None:
    """Годы опыта, если CV называет их явно («опыт работы 7 лет», «более 5 лет опыта»); иначе
    None (не выдумываем). Сначала раздел «Профиль», где пишут общий стаж, затем весь текст."""
    full = text or ""
    for part in (parse_sections(full).get("profile", ""), full):
        years = [int(m.group(1) or m.group(2)) for m in _YEARS.finditer(part)]
        years = [y for y in years if 0 < y <= 60]
        if years:
            return max(years)
    return None
