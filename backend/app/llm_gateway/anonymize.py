"""Конвейер обезличивания текста перед отправкой во внешнюю LLM (FR-023, ADR-007).

Этапы: regex (email, телефоны, URL и голые домены, @логины) -> словари (клиенты, ФИО
сотрудников, проекты, локации) -> NER (ФИО, организации, локации; необязателен). Пересечения
разрешаются в пользу более раннего этапа и более длинного фрагмента. Одному оригиналу
соответствует один плейсхолдер; таблица подстановок возвращается вызывающему и хранится
только до конца задачи.

Модуль чистый: без БД и сети; NER приходит через порт ``NerPort``.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from .technologies import build_terms, is_tech, normalize

#: вид сущности -> метка в плейсхолдере
PERSON, COMPANY, PROJECT, LOCATION = "person", "company", "project", "location"
EMAIL, PHONE, URL, CONTACT = "email", "phone", "url", "contact"
LABELS = {
    PERSON: "ПЕРСОНА", COMPANY: "КОМПАНИЯ", PROJECT: "ПРОЕКТ", LOCATION: "ЛОКАЦИЯ",
    EMAIL: "EMAIL", PHONE: "ТЕЛЕФОН", URL: "ССЫЛКА", CONTACT: "КОНТАКТ",
}
MIN_TERM_LEN = 3  # короче - слишком много ложных срабатываний

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'\]\[)]+", re.IGNORECASE)
# логин мессенджера (@handle): не внутри e-mail и не после слова; 4-32 знака
_HANDLE_RE = re.compile(r"(?<![\w.@])@[A-Za-z][A-Za-z0-9_]{3,31}(?![\w@])")
# голый домен (без схемы и www): только с известным TLD строчными буквами, чтобы не задевать
# «node.js», «ASP.NET», «a.b», имена файлов (main.py, README.md) и версии (1.2.3)
_BARE_TLDS = (
    "com|net|org|info|biz|ru|su|рф|ua|by|kz|io|dev|app|tech|ai|cloud|online|site|store|pro|"
    "test|example|invalid|local|internal"
)
_DOMAIN_RE = re.compile(
    rf"(?<![\w@.\-/])(?:[^\W_](?:[\w\-]*[^\W_])?\.)+(?:{_BARE_TLDS})(?![\w\-]|\.\w)"
    r"(?:/[^\s<>\"'\]\[)]*)?"
)
#: технологии, которые по написанию похожи на домен (регистр не важен)
_DOMAIN_LIKE_TECH = frozenset({"socket.io", "asp.net", "ado.net", "vb.net", "f.net", "dot.net"})
_PHONE_RES = (
    # +7 / 8 и 10 цифр с любыми разделителями
    re.compile(r"(?<![\w])(?:\+7|8|7)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"),
    # международный формат
    re.compile(r"(?<![\w])\+\d[\d\s().\-]{8,16}\d(?!\d)"),
)


@dataclass(frozen=True)
class Dictionaries:
    """Словари обезличивания. Значения - строки как в источнике (ФИО целиком, имя клиента)."""

    persons: tuple[str, ...] = ()
    companies: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()

    def merged(self, other: Dictionaries) -> Dictionaries:
        return Dictionaries(
            persons=_uniq(self.persons + other.persons),
            companies=_uniq(self.companies + other.companies),
            projects=_uniq(self.projects + other.projects),
            locations=_uniq(self.locations + other.locations),
        )

    def is_empty(self) -> bool:
        return not (self.persons or self.companies or self.projects or self.locations)


def _uniq(items: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for it in items:
        s = " ".join(it.split())
        if s:
            seen.setdefault(s.lower(), s)
    return tuple(seen.values())


@dataclass(frozen=True)
class NerSpan:
    kind: str  # PERSON | COMPANY | LOCATION
    start: int
    end: int


class NerPort(Protocol):
    def extract(self, text: str) -> list[NerSpan]: ...


class AnonymizationError(Exception):
    """Обезличивание не завершено чисто: отправка запрещена."""


@dataclass
class AnonymizedText:
    text: str
    #: плейсхолдер -> оригинал; только в памяти / шифртекстом на время задачи
    mapping: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)  # вид -> число уникальных подстановок
    ner_used: bool = False

    @property
    def replacements(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    kind: str
    key: str  # каноническое значение: по нему выдаётся один плейсхолдер
    priority: int  # меньше - сильнее


def _flex(term: str) -> str:
    return r"\s+".join(re.escape(p) for p in term.split())


#: слова, которыми админ часто начинает название; в тексте они могут стоять в другой форме
_GENERIC_PREFIXES = frozenset(
    {"ооо", "ао", "зао", "пао", "оао", "ип", "проект", "компания", "клиент", "заказчик"}
)
_QUOTES = str.maketrans("", "", "«»\"“”„'")


def _variants(term: str) -> list[str]:
    """Название как есть, без кавычек и без ведущих общих слов («ООО «Альфа»» -> «Альфа»)."""
    out = [term]
    plain = " ".join(term.translate(_QUOTES).split())
    words = plain.split()
    if plain != term:
        out.append(plain)
    while len(words) > 1 and words[0].lower() in _GENERIC_PREFIXES:
        words = words[1:]
        out.append(" ".join(words))
    return [v for v in out if len(v) >= MIN_TERM_LEN]


#: короткие слова ФИО (2-3 буквы: Ким, Ли, Пак): только с заглавной буквы и закрытым списком
#: падежных окончаний - иначе «ли», «как» и т.п. давали бы ложные срабатывания (KON-71)
_SHORT_TOKEN_LEN = 2
_SHORT_ENDINGS = "а|у|ом|ем|е|ы|и|ой|ею|ою|ю|я|ей|ов|ев|ам|ами|ах"


def _stem(token: str) -> str:
    return token[:-1] if len(token) >= 5 else token


class _DictMatcher:
    """Регулярные выражения по словарям; строятся один раз на объект Anonymizer."""

    def __init__(self, d: Dictionaries) -> None:
        # (вид, приоритет, шаблон, нормализованное слово -> каноническое значение, по основе?)
        self._rules: list[tuple[str, int, re.Pattern[str], dict[str, str], bool]] = []
        self._add_plain(COMPANY, d.companies)
        self._add_plain(PROJECT, d.projects)
        self._add_plain(LOCATION, d.locations)
        self._add_persons(d.persons)

    def _add_plain(self, kind: str, terms: Iterable[str]) -> None:
        canon: dict[str, str] = {}
        for t in _uniq(terms):
            for v in _variants(t):
                canon.setdefault(v.lower(), t)
        if canon:
            alt = "|".join(_flex(t) for t in sorted(canon, key=len, reverse=True))
            pat = re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.IGNORECASE | re.UNICODE)
            self._rules.append((kind, 1, pat, canon, False))

    def _add_persons(self, names: Iterable[str]) -> None:
        full: dict[str, str] = {}
        parts: dict[str, str] = {}  # отдельное слово ФИО -> каноническое ФИО
        short: dict[str, str] = {}  # то же для слов короче 4 букв
        for name in _uniq(names):
            if len(name) < MIN_TERM_LEN:
                continue
            full[name.lower()] = name
            for tok in re.findall(r"[\w-]+", name):
                if len(tok) >= 4:
                    parts.setdefault(tok.lower(), name)
                elif len(tok) >= _SHORT_TOKEN_LEN and tok.isalpha():
                    short.setdefault(tok.lower(), name)
        if full:
            alt = "|".join(_flex(t) for t in sorted(full.values(), key=len, reverse=True))
            pat = re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.IGNORECASE | re.UNICODE)
            self._rules.append((PERSON, 1, pat, full, False))
        if parts:
            # отдельное слово ФИО с возможным падежным окончанием (Петров -> Петрова, Петрову)
            alt = "|".join(re.escape(_stem(t)) + r"\w{0,2}"
                           for t in sorted(parts, key=len, reverse=True))
            pat = re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.IGNORECASE | re.UNICODE)
            self._rules.append((PERSON, 2, pat, parts, True))
        if short:
            forms = {f for t in short for f in (t.capitalize(), t.upper())}
            alt = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
            pat = re.compile(rf"(?<!\w)(?:{alt})(?i:(?:{_SHORT_ENDINGS}))?(?!\w)", re.UNICODE)
            self._rules.append((PERSON, 2, pat, short, True))

    def find(self, text: str) -> list[_Span]:
        out: list[_Span] = []
        for kind, prio, pat, canon, by_stem in self._rules:
            for m in pat.finditer(text):
                surface = " ".join(m.group(0).split()).lower()
                key = canon.get(surface)
                if key is None and by_stem:
                    key = _canon_by_stem(surface, canon)
                out.append(_Span(m.start(), m.end(), kind, key or m.group(0), prio))
        return out


def _canon_by_stem(surface: str, canon: dict[str, str]) -> str | None:
    best: tuple[str, str] | None = None
    for word, value in canon.items():
        if surface.startswith(_stem(word)) and (best is None or len(word) > len(best[0])):
            best = (word, value)
    return best[1] if best else None


_PLACEHOLDER_RE = re.compile(r"\[(?:%s)_\d+\]" % "|".join(LABELS.values()))


class Anonymizer:
    """Обезличивает текст по regex, словарям и (если задан) NER."""

    def __init__(
        self, dictionaries: Dictionaries, ner: NerPort | None = None,
        tech_terms: Iterable[str] = (),
    ) -> None:
        self._matcher = _DictMatcher(dictionaries)
        self._ner = ner
        # технологии (KON-72): встроенный список + расширение; термины словаря обезличивания
        # имеют приоритет - их белый список не защищает
        own = {
            normalize(v)
            for names in (dictionaries.persons, dictionaries.companies,
                          dictionaries.projects, dictionaries.locations)
            for term in names for v in _variants(term)
        }
        self._tech = frozenset(build_terms(tech_terms) - own)

    def is_known_tech(self, fragment: str) -> bool:
        """Фрагмент - название технологии из белого списка (NER-замена не нужна)."""
        return is_tech(fragment, self._tech)

    def anonymize(self, text: str) -> AnonymizedText:
        spans = self._regex_spans(text)
        spans += self._matcher.find(text)
        ner_used = False
        if self._ner is not None:
            ner_used = True
            for s in self._ner.extract(text):
                surface = text[s.start:s.end]
                if len(surface.strip()) >= MIN_TERM_LEN and not self.is_known_tech(surface):
                    spans.append(_Span(s.start, s.end, s.kind, " ".join(surface.split()), 3))
        chosen = _resolve(spans)

        numbers: Counter[str] = Counter()
        placeholders: dict[tuple[str, str], str] = {}
        mapping: dict[str, str] = {}
        parts: list[str] = []
        pos = 0
        for sp in chosen:
            ident = (sp.kind, sp.key.lower())
            ph = placeholders.get(ident)
            if ph is None:
                numbers[sp.kind] += 1
                ph = f"[{LABELS[sp.kind]}_{numbers[sp.kind]}]"
                placeholders[ident] = ph
                mapping[ph] = sp.key
            parts.append(text[pos:sp.start])
            parts.append(ph)
            pos = sp.end
        parts.append(text[pos:])
        return AnonymizedText(
            text="".join(parts), mapping=mapping, counts=dict(numbers), ner_used=ner_used
        )

    @staticmethod
    def _regex_spans(text: str) -> list[_Span]:
        spans: list[_Span] = []
        for m in _EMAIL_RE.finditer(text):
            spans.append(_Span(m.start(), m.end(), EMAIL, m.group(0), 0))
        for m in _URL_RE.finditer(text):
            end = m.end()
            while end > m.start() and text[end - 1] in ".,;:!?":
                end -= 1
            spans.append(_Span(m.start(), end, URL, text[m.start():end], 0))
        for pat in _PHONE_RES:
            for m in pat.finditer(text):
                spans.append(_Span(m.start(), m.end(), PHONE, m.group(0).strip(), 0))
        for m in _HANDLE_RE.finditer(text):
            spans.append(_Span(m.start(), m.end(), CONTACT, m.group(0), 0))
        for m in _DOMAIN_RE.finditer(text):
            end = m.end()
            while end > m.start() and text[end - 1] in ".,;:!?":
                end -= 1
            if text[m.start():end].lower() in _DOMAIN_LIKE_TECH:
                continue
            spans.append(_Span(m.start(), end, URL, text[m.start():end], 0))
        return spans

    def find_leaks(self, outgoing: str) -> list[str]:
        """Что в исходящем тексте всё ещё опознаётся как ПДн (regex и словари; NER не повторяется).

        Плейсхолдеры перед проверкой вырезаются: слово «ПРОЕКТ» в метке - не утечка.
        """
        clean = _PLACEHOLDER_RE.sub(" ", outgoing)
        spans = self._regex_spans(clean) + self._matcher.find(clean)
        return [clean[s.start:s.end] for s in spans]


def _resolve(spans: list[_Span]) -> list[_Span]:
    """Непересекающийся набор: приоритет этапа, затем длина, затем раннее начало."""
    chosen: list[_Span] = []
    for sp in sorted(spans, key=lambda s: (s.priority, -(s.end - s.start), s.start)):
        if sp.end <= sp.start:
            continue
        if all(sp.end <= c.start or sp.start >= c.end for c in chosen):
            chosen.append(sp)
    return sorted(chosen, key=lambda s: s.start)


def restore(text: str, mapping: dict[str, str]) -> str:
    """Обратная подстановка плейсхолдеров в ответе LLM.

    Точное совпадение ``[ПЕРСОНА_1]``; терпимо к потере скобок и регистру
    (``ПЕРСОНА_1``, ``[Персона 1]``), если такой номер есть в таблице.
    """
    if not mapping or not text:
        return text
    by_id: dict[tuple[str, str], str] = {}
    for ph, original in mapping.items():
        label, num = ph.strip("[]").rsplit("_", 1)
        by_id[(label.upper(), num)] = original
    labels = "|".join(sorted({k[0] for k in by_id}, key=len, reverse=True))
    pat = re.compile(
        rf"\[\s*({labels})[_\s\-]?(\d+)\s*\]|(?<!\w)({labels})[_\s\-]?(\d+)(?!\d)",
        re.IGNORECASE,
    )

    def sub(m: re.Match[str]) -> str:
        label, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return by_id.get((label.upper(), num), m.group(0))

    return pat.sub(sub, text)
