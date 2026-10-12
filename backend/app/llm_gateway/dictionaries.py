"""Источники словарей обезличивания: кэш Jira (клиенты), CV (ФИО), словарь администратора.

Словарь администратора - app_setting ``llm_dictionary`` (JSON): дополнительные названия
клиентов, проектов, ФИО и локаций, которых нет в источниках (список «запрещённых строк»).
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

from .anonymize import Dictionaries
from .repo import LlmRepo

log = logging.getLogger(__name__)

SETTING_KEY = "llm_dictionary"
MAX_TERMS = 5000
MAX_TERM_LEN = 200
KINDS = ("persons", "companies", "projects", "locations")


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
