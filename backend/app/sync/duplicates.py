"""Выявление дублей потребностей (FR-014 v1.1).

Детерминированное сравнение текстов, описывающих требования к кандидату (описание +
customfield_10811): доля текста, покрытая непрерывными совпадающими фрагментами
длиной от 3 токенов (последовательное изложение, difflib). Порог 15% - решение
заказчика (ISS-005); сравнение по статусу, не по резолюции (БП-10); окно 2 недели.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

#: Порог «дубль с очень высокой вероятностью» (ISS-005). В UI не выносится (KON-165).
THRESHOLD_PERCENT = 15
#: Минимальный непрерывный фрагмент совпадения, токенов (последовательное изложение).
_MIN_RUN = 3
#: Минимальная длина токена: короче — шум («и», «в»).
_MIN_TOKEN_LEN = 2

_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)


def tokens(text: str | None) -> list[str]:
    """Токены нормализованного текста: слова (буквы/цифры) в нижнем регистре от 2 символов."""
    if not text:
        return []
    return [t for t in (w.lower() for w in _TOKEN_RE.findall(text)) if len(t) >= _MIN_TOKEN_LEN]


def _side_percent(text: str | None, other: str | None) -> int:
    """Сколько процентов токенов ``text`` покрыто фрагментами совпадения от 3 токенов."""
    ta, tb = tokens(text), tokens(other)
    if not ta:
        return 0
    matched = sum(
        b.size for b in SequenceMatcher(None, ta, tb, autojunk=False).get_matching_blocks()
        if b.size >= _MIN_RUN
    )
    return (100 * matched) // len(ta)


def match_percent(text_a: str | None, text_b: str | None) -> int:
    """Процент совпадения пары текстов: максимум покрытия обеих сторон (0..100)."""
    return max(_side_percent(text_a, text_b), _side_percent(text_b, text_a))


def canonical_pair(key_a: str, key_b: str) -> tuple[str, str]:
    """Пара в каноническом порядке (лексикографически) - одна строка на связь в БД."""
    return (key_a, key_b) if key_a <= key_b else (key_b, key_a)
