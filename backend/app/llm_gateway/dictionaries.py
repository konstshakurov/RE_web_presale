"""Источники словарей обезличивания: кэш Jira (клиенты), CV (ФИО), словарь администратора.

Словарь администратора - app_setting ``llm_dictionary`` (JSON): дополнительные названия
клиентов, проектов, ФИО и локаций, которых нет в источниках (список «запрещённых строк»).
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

from .anonymize import Dictionaries, _variants
from .repo import LlmRepo
from .technologies import normalize

log = logging.getLogger(__name__)

SETTING_KEY = "llm_dictionary"
TECH_SETTING_KEY = "llm_tech_terms"
MAX_TERMS = 5000
MAX_TERM_LEN = 200
KINDS = ("persons", "companies", "projects", "locations")
MAX_TECH_TERMS = 500
MAX_TECH_TERM_LEN = 64


class EmployeeNames(Protocol):
    def list_employee_names(self) -> list[str]: ...


class DictionarySource:
    def __init__(self, repo: LlmRepo, cv: EmployeeNames | None = None) -> None:
        self._repo = repo
        self._cv = cv

    def load_custom(self) -> Dictionaries:
        raw = self._repo.get_setting(SETTING_KEY)
        if not raw:
            return Dictionaries()
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("app_setting %s: не JSON, словарь пропущен", SETTING_KEY)
            return Dictionaries()
        return Dictionaries(**{k: tuple(str(v) for v in data.get(k, [])) for k in KINDS})

    def load_tech_terms(self) -> tuple[str, ...]:
        """Расширение белого списка технологий (KON-72): app_setting ``llm_tech_terms``,
        JSON-массив строк. Встроенный список - в ``technologies.py``."""
        raw = self._repo.get_setting(TECH_SETTING_KEY)
        if not raw:
            return ()
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("app_setting %s: не JSON, расширение пропущено", TECH_SETTING_KEY)
            return ()
        if not isinstance(data, list):
            return ()
        return tuple(str(v)[:MAX_TERM_LEN] for v in data[:MAX_TERMS] if str(v).strip())

    def save_tech_terms(self, terms: list[str]) -> None:
        """Сохранить расширение белого списка технологий; действует на следующий запрос
        обезличивания (список читается при каждом обезличивании), перезапуск не нужен."""
        self._repo.put_setting(TECH_SETTING_KEY, json.dumps(list(terms), ensure_ascii=False))

    def tech_overlaps(self, terms: list[str]) -> list[str]:
        """Термины, которые есть и в словаре обезличивания: приоритет у словаря, поэтому
        белый список их не защищает (они маскируются всегда)."""
        d = self.load_custom()
        own = {
            normalize(v)
            for kind in KINDS for term in getattr(d, kind) for v in _variants(term)
        }
        return [t for t in terms if normalize(t) in own]

    def save_custom(self, d: Dictionaries) -> None:
        self._repo.put_setting(
            SETTING_KEY,
            json.dumps({k: list(getattr(d, k)) for k in KINDS}, ensure_ascii=False),
        )

    def load(self) -> Dictionaries:
        """Все словари: кэш Jira + сотрудники CV + словарь администратора."""
        src = self._repo.dictionary_sources()
        persons = list(src["persons"])
        if self._cv is not None:
            persons += self._cv.list_employee_names()
        base = Dictionaries(persons=tuple(persons), companies=tuple(src["companies"]))
        return base.merged(self.load_custom())
