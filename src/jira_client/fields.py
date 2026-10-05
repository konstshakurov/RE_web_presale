# -*- coding: utf-8 -*-
"""Идентификаторы полей Jira, нужные интеграции re_web_presale.

Источники идентификаторов (не выдуманы):

* ``FR-015`` — перечисляет поля правил и условий клиента по ID;
* ``FR-028`` — фиксирует ``Epic Link = customfield_10101`` и то, что Jira не
  отдаёт ``parent`` и ``priority``;
* ``input/jira/_api_notes.md`` и ``input/jira/probe_raw/`` — подтверждённые
  ответы Jira (ставка, статусы, отсутствие ключей).

**Важно.** ``priority`` и ``parent`` в ответах задач *отсутствуют как ключи*,
а не равны ``null``. Интеграция обязана читать их через ``.get()`` и не
рассчитывать на наличие. Проба это подтвердила: в выгрузке трёх задач ключей
``priority`` и ``parent`` нет ни в одном ответе.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Системные поля
# ---------------------------------------------------------------------------

FIELD_SUMMARY = "summary"
FIELD_DESCRIPTION = "description"
FIELD_STATUS = "status"
FIELD_ISSUETYPE = "issuetype"
FIELD_PROJECT = "project"
FIELD_CREATED = "created"
FIELD_UPDATED = "updated"
FIELD_REPORTER = "reporter"
FIELD_ASSIGNEE = "assignee"
FIELD_LABELS = "labels"
FIELD_COMPONENTS = "components"
FIELD_ATTACHMENT = "attachment"
FIELD_COMMENT = "comment"
FIELD_ISSUELINKS = "issuelinks"
FIELD_SUBTASKS = "subtasks"
#: Jira Server 8.13.0 не отдаёт эти два поля в задаче — см. FR-028.
FIELD_PRIORITY = "priority"
FIELD_PARENT = "parent"
#: Отсутствуют в выдаче задачи, но присутствуют в метаданных полей.
FIELD_CREATED_DATE = "created"

#: Поля, которые клиент запрашивает у поиска по умолчанию (FR-028: «запрос
#: только нужных полей»). Объём ответа Jira для трёх задач — 625–666 полей,
#: поэтому явный список обязателен.
DEFAULT_SEARCH_FIELDS: Tuple[str, ...] = (
    FIELD_SUMMARY,
    FIELD_STATUS,
    FIELD_ISSUETYPE,
    FIELD_UPDATED,
    FIELD_CREATED,
    FIELD_ASSIGNEE,
)

# ---------------------------------------------------------------------------
# GreenHopper / Agile
# ---------------------------------------------------------------------------

#: Связь «эпик → потребность». FR-028: Epic Link = customfield_10101.
EPIC_LINK = "customfield_10101"
#: Проба: ``"Epic Link" = <эпик>`` и ``cf[10101] = "<эпик>"`` дают одинаковый
#: результат (282 задачи). ``parent = <эпик>`` даёт 0 — использовать нельзя.
EPIC_LINK_FIELD = EPIC_LINK

#: Смежные поля эпика, найденные в выдаче. Проба подтвердила наличие ключей,
#: но не подтвердила, что они поддерживают фильтрацию JQL.
CF_EPIC_SA = "customfield_22400"
CF_EPIC_LINK_NEEDS = "customfield_11032"

# ---------------------------------------------------------------------------
# Ставка (FR-018) — четыре поля подряд, значения могут быть числовыми
# ---------------------------------------------------------------------------

RATE_MIN = "customfield_20809"
RATE_MAX = "customfield_20810"
RATE_WITH_VAT = "customfield_20811"
RATE_CURRENCY = "customfield_20812"
RATE_FIELDS: Tuple[str, ...] = (RATE_MIN, RATE_MAX, RATE_WITH_VAT, RATE_CURRENCY)

# ---------------------------------------------------------------------------
# Правила и условия клиента (FR-015, ISS-009)
# ---------------------------------------------------------------------------

#: Поля эпика клиента. FR-015: «показывать только заполненные поля».
EPIC_RULES: Mapping[str, str] = {
    "customfield_12000": "Рекомендации",
    "customfield_21801": "Условия для производства",
    "customfield_20909": "Критерии приемки",
    "customfield_20910": "Штрафы",
    "customfield_20907": "Привлечение третьих лиц",
    "customfield_20913": "Разрешение на PR",
    "customfield_20903": "Постановка задач",
    "customfield_20904": "РИД",
    "customfield_20905": "Обязанности Заказчика",
    "customfield_20906": "Автоприемка",
    "customfield_14024": "Формат работы",
    "customfield_14023": "Тип договора",
    "customfield_14205": "NDA",
}

#: Поля правил на самой потребности.
DEMAND_RULES: Mapping[str, str] = {
    "customfield_20302": "Рисовать опыт нельзя",
    "customfield_20914": "Работа вне РФ",
    "customfield_11506": "Нужно предоставить CV",
    "customfield_10811": "Требования к кандидату",
}

RULE_FIELDS: Tuple[str, ...] = tuple(EPIC_RULES) + tuple(DEMAND_RULES)

#: Поля потребности, которые интеграция читает целиком.
DEMAND_READ_FIELDS: Tuple[str, ...] = (
    FIELD_SUMMARY,
    FIELD_DESCRIPTION,
    FIELD_STATUS,
    FIELD_ISSUETYPE,
    FIELD_UPDATED,
    FIELD_CREATED,
    FIELD_ASSIGNEE,
    EPIC_LINK,
) + RATE_FIELDS + tuple(DEMAND_RULES)

#: Поля эпика, которые интеграция читает целиком.
EPIC_READ_FIELDS: Tuple[str, ...] = (
    FIELD_SUMMARY,
    FIELD_DESCRIPTION,
    FIELD_UPDATED,
) + tuple(EPIC_RULES)

# ---------------------------------------------------------------------------
# Типы задач
# ---------------------------------------------------------------------------

#: Проекты, участвующие в интеграции.
PROJECT_DEMAND = "ANALYTICS"
PROJECT_EPIC = "SA"

#: Типы задач из выгрузки. Имена в русской локали экземпляра — не путать.
ISSUE_TYPE_DEMAND = "Demand"
ISSUE_TYPE_EPIC = "Epic"


# ---------------------------------------------------------------------------
# Разрешение имён полей в ID
# ---------------------------------------------------------------------------

#: Отображаемое имя для каждого известного поля.
#: Русские имена взяты из FR-015 дословно; имена Agile-полей — из выдачи Jira.
DISPLAY_NAMES: Mapping[str, str] = {
    EPIC_LINK: "Epic Link",
    CF_EPIC_SA: "Epic SA",
    CF_EPIC_LINK_NEEDS: "Epic link потребности",
}
DISPLAY_NAMES.update({field_id: name for field_id, name in EPIC_RULES.items()})
DISPLAY_NAMES.update({field_id: name for field_id, name in DEMAND_RULES.items()})

#: «Отображаемое имя → ID». Нужны, потому что JQL и метаданные Jira оперируют
#: именами, а интеграция думает ID.
NAME_TO_ID: Mapping[str, str] = {
    name: field_id for field_id, name in DISPLAY_NAMES.items()
}

#: «ID → отображаемое имя» (для логов, отчётов и сообщений об ошибках).
ID_TO_NAME: Mapping[str, str] = dict(DISPLAY_NAMES)


def resolve_field_id(name_or_id: str) -> Optional[str]:
    """Привести отображаемое имя поля к его ``customfield_*`` ID.

    Возвращает ``None``, если поле неизвестно. Проба показала, что на этом
    экземпляре 833 поля, поэтому молчаливое угадывание недопустимо: лучше
    ``None``, чем неверный ID в JQL.
    """
    if not name_or_id:
        return None
    value = name_or_id.strip()
    if value.startswith("customfield_"):
        return value
    if value in NAME_TO_ID:
        return NAME_TO_ID[value]
    lowered = value.lower()
    for name, field_id in NAME_TO_ID.items():
        if name.lower() == lowered:
            return field_id
    return None


def humanize(field_id: str) -> str:
    """Отображаемое имя поля или сам ID, если имя неизвестно.

    ``humanize("customfield_14205")`` → ``"NDA"``;
    ``humanize("customfield_99999")`` → ``"customfield_99999"`` (без угадывания).
    """
    return ID_TO_NAME.get(field_id, field_id)


def build_field_index(fields: Iterable[Mapping[str, object]]) -> Dict[str, Mapping]:
    """Индекс метаданных полей по ID.

    ``GET /rest/api/2/field`` на этом экземпляре возвращает 833 записи — больше,
    чем помещается в UI, поэтому метаданные загружаются один раз и кэшируются.
    """
    index: Dict[str, Mapping] = {}
    for item in fields or ():
        if not isinstance(item, Mapping):
            continue
        key = item.get("id")
        if isinstance(key, str):
            index[key] = item
    return index


def field_name_from_meta(item: Mapping) -> str:
    """Отображаемое имя поля из метаданных: сначала ``name``, затем ``id``."""
    name = item.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return str(item.get("id") or "")


def pick_filled(fields: Mapping, wanted: Iterable[str]) -> List[Tuple[str, object, str]]:
    """Выбрать только заполненные поля — требование FR-015.

    ``None``, пустая строка и пустой список считаются незаполненными.
    Возвращает тройки ``(id, значение, отображаемое имя)``.
    """
    result: List[Tuple[str, object, str]] = []
    for field_id in wanted:
        if field_id not in fields:
            continue
        value = fields[field_id]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, dict)) and len(value) == 0:
            continue
        result.append((field_id, value, humanize(field_id)))
    return result