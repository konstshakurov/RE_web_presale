"""Правила групп статусов без БД: зарезервированные имена, известные статусы, «Прочие»."""
from __future__ import annotations

from collections.abc import Iterable

#: Виртуальные группы (FR-035): считаются в app.sync, в таблицы не кладутся, не редактируются.
RESERVED_NAMES = ("Прочие", "Все статусы")

#: 19 статусов workflow Jira «Department requirements workflow» (FR-035): 10 из групп по
#: умолчанию и 9 «прочих». Источник списка для выбора; к ним добавляются статусы из кэша.
KNOWN_JIRA_STATUSES = (
    "Требуется кандидат", "Поиск кандидата", "Найм", "Отказ по кандидату",
    "Интервью не пройдено", "Кандидат назначен", "Подготовка к интервью",
    "Интервью состоялось", "Интервью пройдено", "Закреплен за проектом",
    "Кандидатов нет", "Поиск партнерки", "Партнерка", "На проекте", "Скоро освобождается",
    "Работа завершена", "Не на проекте", "Неактуально", "Отложено",
)


def is_reserved(name: str) -> bool:
    return name.strip().casefold() in {n.casefold() for n in RESERVED_NAMES}


def other_statuses(all_statuses: Iterable[str], grouped: Iterable[str]) -> list[str]:
    """Статусы, не вошедшие ни в одну группу, - они попадают в «Прочие»."""
    in_groups = set(grouped)
    return sorted({s for s in all_statuses if s not in in_groups})
