"""Детерминированный предварительный отбор кандидатов и разбиение на пакеты (FR-005).

LLM оценивает только отобранных: отбор по статусу и наличию основного CV делает SQL, здесь -
порядок по числу навыков CV, встречающихся в тексте потребности, и ограничение числа
оцениваемых кандидатов. Без обращений к БД и сети.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: Не более стольких кандидатов уходит в LLM за один прогон (допущение: ~80 сотрудников, ADR-009)
MAX_EVALUATED = 30
#: Пакет: не более стольких кандидатов и не более стольких символов запроса
BATCH_MAX_CANDIDATES = 5
BATCH_MAX_CHARS = 24_000


@dataclass(frozen=True)
class Scored:
    key: str
    hits: int  # сколько навыков кандидата упомянуто в потребности


def need_text(title: str, description: str | None, requirements: str | None,
              tags: Iterable[str] = ()) -> str:
    return "\n".join([title or "", description or "", requirements or "", *tags]).lower()


def count_hits(skills: Iterable[str], text: str) -> int:
    """Навык засчитывается, если встречается в тексте потребности как отдельное слово/фраза."""
    n = 0
    for tag in set(skills):
        t = tag.strip().lower()
        if t and re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text):
            n += 1
    return n


def preselect(
    candidates: Sequence[tuple[str, str, Sequence[str]]], text: str, limit: int = MAX_EVALUATED,
) -> list[Scored]:
    """``candidates``: (ключ, ФИО, навыки). Все, если их не больше ``limit``; иначе лучшие по
    числу совпадений (ничья - по ФИО, затем по ключу). Результат упорядочен так же."""
    scored = [(Scored(k, count_hits(skills, text)), name) for k, name, skills in candidates]
    scored.sort(key=lambda x: (-x[0].hits, x[1], x[0].key))
    return [s for s, _ in scored[:limit]]


def split_batches(
    sizes: Sequence[int], *, max_items: int = BATCH_MAX_CANDIDATES,
    max_chars: int = BATCH_MAX_CHARS,
) -> list[list[int]]:
    """Индексы кандидатов по пакетам; ``sizes`` - длина фрагмента запроса на кандидата.
    Кандидат крупнее лимита идёт отдельным пакетом."""
    batches: list[list[int]] = []
    cur: list[int] = []
    cur_chars = 0
    for i, size in enumerate(sizes):
        if cur and (len(cur) >= max_items or cur_chars + size > max_chars):
            batches.append(cur)
            cur, cur_chars = [], 0
        cur.append(i)
        cur_chars += size
    if cur:
        batches.append(cur)
    return batches
