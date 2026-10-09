"""Преобразование ответов Jira в строки кэша. Чистые функции, без БД и сети.

Поля Jira: Epic Link = customfield_10101; ``parent`` и ``priority`` не используются (FR-028).
Все ключи читаются через ``.get()``: Jira Server 8.13.0 отсутствующие поля не присылает вовсе.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")

F_EPIC_LINK = "customfield_10101"
F_REQUIREMENTS = "customfield_10811"
F_CONTRACT = "customfield_19201"
F_RATE = "customfield_20809"
F_RATE_VAT = "customfield_20810"
F_RATE_CURRENCY = "customfield_20811"
F_RATE_UNIT = "customfield_20812"
F_RATE_PROPOSER = "customfield_21105"
F_EXTRA_EXPERIENCE = "customfield_13504"

#: Только нужные поля (FR-028): без списка Jira отдаёт сотни полей.
NEED_FIELDS: tuple[str, ...] = (
    "summary", "description", "status", "created", "updated", "assignee", "reporter",
    F_EPIC_LINK, F_REQUIREMENTS, F_CONTRACT,
    F_RATE, F_RATE_VAT, F_RATE_CURRENCY, F_RATE_UNIT, F_RATE_PROPOSER,
)
EMPLOYEE_FIELDS: tuple[str, ...] = ("summary", "status", "updated", F_EXTRA_EXPERIENCE)

#: Правила и условия клиента на эпике (FR-015): поле Jira -> название для пользователя.
EPIC_RULE_FIELDS: dict[str, str] = {
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
EPIC_FIELDS: tuple[str, ...] = ("summary", *EPIC_RULE_FIELDS)

#: Сотрудник в этих статусах не назначается на потребность (FR-002).
NOT_ASSIGNABLE_STATUSES = frozenset(
    {"Коммерческий проект", "Недоступен для подключения", "Уволен", "Декрет"}
)

CONTRACT_TYPES = frozenset({"Прямой контракт", "Субподрядчик"})
#: Валюты Jira -> допустимые схемой jira_need. EUR/GBR схема не принимает (см. отчёт KON-35).
CURRENCIES = {"руб.": "RUB", "руб": "RUB", "rub": "RUB", "usd": "USD", "kzt": "KZT"}


@dataclass(frozen=True)
class NeedRow:
    jira_key: str
    title: str
    description: str | None
    requirements_text: str | None
    status: str
    epic_key: str | None
    assignee: str | None
    author: str | None
    contract_type: str | None
    rate_amount: Decimal | None
    rate_currency: str | None
    rate_includes_vat: bool | None
    rate_unit: str | None
    rate_proposer: str | None
    jira_created_at: datetime
    jira_updated_at: datetime
    rate_dropped: bool = False  # ставка в Jira есть, но схема её не принимает


@dataclass(frozen=True)
class EmployeeRow:
    jira_key: str
    full_name: str
    status: str
    assignable: bool
    extra_experience: str | None


def parse_jira_dt(value: Any) -> datetime | None:
    """``2026-09-30T13:04:48.495+0400`` -> aware datetime; без пояса считается UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def jql_time(moment: datetime, utc_offset_hours: float) -> str:
    """Время для JQL ``updated > "YYYY-MM-DD HH:mm"`` в поясе Jira, с округлением вниз."""
    local = moment.astimezone(UTC) + timedelta(hours=utc_offset_hours)
    return local.strftime("%Y-%m-%d %H:%M")


def _text(value: Any) -> str | None:
    """Строка из строки/option/user/списка; пустое -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for k in ("value", "name", "displayName"):
            if isinstance(value.get(k), str) and value[k].strip():
                return value[k].strip()
        return None
    if isinstance(value, list | tuple):
        return ", ".join(t for t in (_text(v) for v in value) if t) or None
    if isinstance(value, int | float):
        return str(value)
    return None


def _login(value: Any) -> str | None:
    if isinstance(value, dict):
        v = value.get("name") or value.get("key")
        return v.strip() if isinstance(v, str) and v.strip() else None
    return None


def _amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or d < 0 or d >= Decimal("1e12"):
        return None
    return d.quantize(Decimal("0.01"))


def _rate(fields: dict[str, Any]):
    """(сумма, валюта, НДС, единица, кто предлагает, отброшена) с учётом CHECK jira_need."""
    amount = _amount(fields.get(F_RATE))
    raw_cur = _text(fields.get(F_RATE_CURRENCY))
    currency = CURRENCIES.get(raw_cur.lower()) if raw_cur else None
    vat_raw = (_text(fields.get(F_RATE_VAT)) or "").lower()
    vat = True if vat_raw == "да" else False if vat_raw == "нет" else None
    unit = _text(fields.get(F_RATE_UNIT))
    prop_raw = (_text(fields.get(F_RATE_PROPOSER)) or "").lower()
    proposer = (
        "от клиента" if "клиент" in prop_raw
        else "от производства" if "производ" in prop_raw
        else None
    )
    if amount is not None and (currency is None or (currency == "RUB" and vat is None)):
        # без допустимой валюты (или без НДС для RUB, BR-003) ставка нарушает схему
        return None, None, None, unit, proposer, True
    if amount is None:
        currency = vat = None
    return amount, currency, vat, unit, proposer, False


def map_need(issue: dict[str, Any]) -> NeedRow | None:
    """Потребность из ответа Jira; None, если валидную строку построить нельзя."""
    key = issue.get("key")
    fields = issue.get("fields")
    if not isinstance(key, str) or not KEY_RE.match(key) or not isinstance(fields, dict):
        return None
    status_obj = fields.get("status")
    status = _text(status_obj.get("name")) if isinstance(status_obj, dict) else None
    created = parse_jira_dt(fields.get("created"))
    updated = parse_jira_dt(fields.get("updated"))
    if not status or created is None or updated is None:
        return None
    epic = fields.get(F_EPIC_LINK)
    epic_key = epic.strip() if isinstance(epic, str) and KEY_RE.match(epic.strip()) else None
    contract = _text(fields.get(F_CONTRACT))
    amount, currency, vat, unit, proposer, dropped = _rate(fields)
    desc = fields.get("description")
    return NeedRow(
        jira_key=key,
        title=_text(fields.get("summary")) or key,
        description=desc if isinstance(desc, str) and desc.strip() else None,
        requirements_text=_text(fields.get(F_REQUIREMENTS)),
        status=status,
        epic_key=epic_key,
        assignee=_login(fields.get("assignee")),
        author=_login(fields.get("reporter")),
        contract_type=contract if contract in CONTRACT_TYPES else None,
        rate_amount=amount,
        rate_currency=currency,
        rate_includes_vat=vat,
        rate_unit=unit,
        rate_proposer=proposer,
        jira_created_at=created,
        jira_updated_at=updated,
        rate_dropped=dropped,
    )


def map_employee(issue: dict[str, Any]) -> EmployeeRow | None:
    key = issue.get("key")
    fields = issue.get("fields")
    if not isinstance(key, str) or not KEY_RE.match(key) or not isinstance(fields, dict):
        return None
    status_obj = fields.get("status")
    status = _text(status_obj.get("name")) if isinstance(status_obj, dict) else None
    name = _text(fields.get("summary"))
    if not status or not name:
        return None
    return EmployeeRow(
        jira_key=key,
        full_name=name,
        status=status,
        assignable=status not in NOT_ASSIGNABLE_STATUSES,
        extra_experience=_text(fields.get(F_EXTRA_EXPERIENCE)),
    )


def map_epic_rules(fields: dict[str, Any]) -> list[dict[str, str]]:
    """Только заполненные поля правил клиента (FR-015), в порядке FR-015."""
    rules = []
    for field_id, title in EPIC_RULE_FIELDS.items():
        value = _text(fields.get(field_id))
        if value:
            rules.append({"id": field_id, "name": title, "value": value})
    return rules
