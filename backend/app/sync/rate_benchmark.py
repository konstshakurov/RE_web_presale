"""Ориентир ставки (FR-019, BR-009, KON-95): средняя и медианная по кэшу app.jira_need.
Данные только из кэша, в Jira запрос не уходит. Расчёт - SQL (avg, percentile_cont)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ..auth.deps import SessionContext, authenticated
from .repo import SyncRepo

router = APIRouter(tags=["sync"], responses={401: {"description": "Требуется вход"}})

DEFAULT_PERIOD_MONTHS = 6
MAX_PERIOD_MONTHS = 60  # ДОПУЩЕНИЕ: верхняя граница периода спецификацией не задана
MIN_GROUP_COUNT = 3  # меньше записей - только count, без mean/median
VAT_SETTING_KEY = "vat_percent"
DEFAULT_VAT_PERCENT = 22.0  # BR-009


class RateGroup(BaseModel):
    currency: str = Field(description="RUB | USD | KZT; валюты не складываются")
    unit: str | None = Field(None, description="Единица измерения ставки; None - не указана")
    count: int = Field(description="Потребностей со ставкой за период")
    mean: float | None = Field(None, description="Среднее; рубли с НДС. None при count < 3")
    median: float | None = Field(None, description="Медиана; рубли с НДС. None при count < 3")


class RateBenchmark(BaseModel):
    period_months: int
    vat_percent: float = Field(description="НДС для приведения рублей к «с НДС» (BR-009)")
    groups: list[RateGroup]
    as_of: datetime = Field(description="Момент расчёта; окно периода считается от него")


def parse_vat(raw: str | None) -> float:
    """Настройка vat_percent: число 0..100; пусто или мусор - умолчание 22 (BR-009)."""
    try:
        value = float((raw or "").strip().replace(",", "."))
    except ValueError:
        return DEFAULT_VAT_PERCENT
    return value if 0 <= value <= 100 else DEFAULT_VAT_PERCENT


def months_ago(now: datetime, months: int) -> datetime:
    """Начало окна: now минус целые календарные месяцы (день сжимается до конца месяца)."""
    total = now.year * 12 + (now.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    first_next = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=now.tzinfo)
    last_day = (first_next - datetime(year, month, 1, tzinfo=now.tzinfo)).days
    return now.replace(year=year, month=month, day=min(now.day, last_day))


def _repo(request: Request) -> SyncRepo:
    return request.app.state.sync.repo


@router.get("/needs/rate-benchmark", response_model=RateBenchmark)
def rate_benchmark(
    request: Request,
    _: SessionContext = Depends(authenticated),
    period_months: Annotated[int, Query(
        ge=1, le=MAX_PERIOD_MONTHS, description="Период по дате создания потребности, месяцев")
    ] = DEFAULT_PERIOD_MONTHS,
) -> RateBenchmark:
    repo = _repo(request)
    now = datetime.now(UTC)
    vat = parse_vat(repo.get_setting(VAT_SETTING_KEY))
    groups = repo.rate_benchmark(months_ago(now, period_months), vat, MIN_GROUP_COUNT)
    return RateBenchmark(
        period_months=period_months, vat_percent=vat,
        groups=[RateGroup(**g) for g in groups], as_of=now,
    )
