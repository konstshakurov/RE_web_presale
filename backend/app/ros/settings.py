"""Параметры модели ROS: читаются на каждый вызов из app.app_setting (как настройки matching);
вне границ или не число - значение по умолчанию. Эталон - ROS_model_v2 (FR-041, KON-161):
165 billable-часов, НДС 22%, взносы 15,2%/7,8% (лимит ФОТ 2 979 000), косвенные 53 000,
налог на прибыль 5% только с прибыли > 0."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

FIELDS = (
    "billable_hours",
    "indirect_cost",
    "profit_tax_rate",
    "vat_rate",
    "insurance_before",
    "insurance_after",
    "fot_limit",
)

# (min, max) каждого параметра: значения редактирует админ в Настройках, вкладка «ROS»
LIMITS: dict[str, tuple[float, float]] = {
    "billable_hours": (1.0, 744.0),
    "indirect_cost": (0.0, 10_000_000.0),
    "profit_tax_rate": (0.0, 100.0),
    "vat_rate": (0.0, 100.0),
    "insurance_before": (0.0, 100.0),
    "insurance_after": (0.0, 100.0),
    "fot_limit": (1.0, 1_000_000_000.0),
}

# Эталонная модель (fr_041: «приложение поставляется со значениями по умолчанию = эталон»)
ROS_DEFAULTS: dict[str, float] = {
    "billable_hours": 165.0,
    "indirect_cost": 53000.0,
    "profit_tax_rate": 5.0,
    "vat_rate": 22.0,
    "insurance_before": 15.2,
    "insurance_after": 7.8,
    "fot_limit": 2_979_000.0,
}


def setting_key(field: str) -> str:
    return "ros_" + field


def _num(raw: str | None, lo: float, hi: float, default: float) -> float:
    try:
        value = float((raw or "").strip().replace(",", "."))
    except ValueError:
        return default
    return value if lo <= value <= hi else default


def check(values: dict[str, float]) -> None:
    """validate(): вне границ - ValueError (router отвечает 400)."""
    for field, (lo, hi) in LIMITS.items():
        if field in values:
            value = values[field]
            if not isinstance(value, (int, float)) or not lo <= float(value) <= hi:
                raise ValueError(f"{field} должно быть числом в {lo}..{hi}")


class StaticRosSettings:
    """Значения в памяти (тесты и запуск без app_db)."""

    def __init__(self, values: dict[str, float] | None = None) -> None:
        self._values = dict(ROS_DEFAULTS)
        if values:
            check(values)
            self._values.update(values)

    def all(self) -> dict[str, float]:
        return dict(self._values)

    def set(self, values: dict[str, float]) -> dict[str, float]:
        check(values)
        self._values.update({f: float(v) for f, v in values.items()})
        return self.all()


class AppRosSettings:
    """Значения из app.app_setting; чтение на каждый вызов (меняются администратором на лету)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _get(self, key: str) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT value FROM app.app_setting WHERE key = %s", (key,)).fetchone()
        return row[0] if row else None

    def all(self) -> dict[str, float]:
        return {
            f: _num(self._get(setting_key(f)), *LIMITS[f], ROS_DEFAULTS[f])
            for f in FIELDS
        }

    def set(self, values: dict[str, float]) -> dict[str, float]:
        check(values)
        with self._pool.connection() as conn, conn.transaction():
            for field in FIELDS:
                if field in values:
                    conn.execute(
                        "INSERT INTO app.app_setting (key, value) VALUES (%s, %s) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                        (setting_key(field), f"{float(values[field]):.10f}"),
                    )
        return self.all()
