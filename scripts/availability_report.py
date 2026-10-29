"""Отчёт доступности /health: будни 9:00-18:00 по местному времени ВМ (A7), шаг 1 минута.

Доступность = минуты с успешной проверкой / все минуты окна. Минуты без записи считаются
недоступными (монитор или ВМ не работали). Праздничные дни не исключаются (допущение).
"""
import argparse
import calendar
import json
import sys
from datetime import datetime, timedelta

ap = argparse.ArgumentParser()
ap.add_argument("--month", required=True, help="YYYY-MM")
ap.add_argument("--log", required=True, help="health-YYYYMM.jsonl")
ap.add_argument("--target", type=float, default=99.0)
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")
y, m = map(int, a.month.split("-"))

slots = {}  # минута (местное время из метки) -> была ли хоть одна успешная проверка
try:
    with open(a.log, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                t = datetime.strptime(r["ts"][:16], "%Y-%m-%dT%H:%M")
            except (ValueError, KeyError):
                continue
            if (t.year, t.month) == (y, m):
                slots[t] = slots.get(t, False) or bool(r.get("ok"))
except FileNotFoundError:
    pass

window = []
for d in range(1, calendar.monthrange(y, m)[1] + 1):
    day = datetime(y, m, d)
    if day.weekday() < 5:
        window += [day + timedelta(hours=9, minutes=i) for i in range(540)]
ok = sum(1 for t in window if slots.get(t) is True)
bad = sum(1 for t in window if slots.get(t) is False)
nodata = len(window) - ok - bad
pct = 100.0 * ok / len(window) if window else 100.0

incidents, cur = [], None
for t in window:
    up = slots.get(t) is True
    if not up and cur is None:
        cur = [t, t]
    elif not up:
        cur[1] = t
    elif cur:
        incidents.append(cur)
        cur = None
if cur:
    incidents.append(cur)

print(f"# Отчёт доступности re_web_presale за {a.month}\n")
print("Окно: пн-пт 09:00-18:00 местного времени ВМ, проверка `/health` раз в минуту.\n")
print(f"- Минут в окне: {len(window)}")
print(f"- Успешных: {ok}; неуспешных: {bad}; без данных: {nodata} (считаются недоступными)")
verdict = "выполнено" if pct >= a.target else "НЕ выполнено"
print(f"- **Доступность: {pct:.2f}%** (цель NFR-003: {a.target:.0f}%) - {verdict}")
if window and nodata > len(window) * 0.5:
    print("\n> Внимание: данных мало, отчёт нерепрезентативен (монитор не работал?).")
print(f"\n## Простои ({len(incidents)})\n")
for s, e in incidents[:50]:
    print(f"- {s:%d.%m %H:%M} - {e:%H:%M}: {int((e - s).total_seconds() // 60) + 1} мин")
if len(incidents) > 50:
    print(f"- ... и ещё {len(incidents) - 50}")
