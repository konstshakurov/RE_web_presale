"""Логика выявления дублей (FR-014 v1.1): токены, порог 15%, последовательное изложение."""
from __future__ import annotations

from app.sync.duplicates import canonical_pair, match_percent, tokens

# Образцы владельца (05.11.2026): прямая потребность и её субподрядные дубли.
MAGNIT = (
    "Подбор аналитиков данных для проекта отчётности. Требования к кандидату: опыт работы "
    "бизнес-аналитиком от 3 лет, инструменты Power BI и SQL, компетенции - сбор требований, "
    "моделирование данных, опыт в ритейле."
)
MAGNIT_SUB_OPEN = (
    "Субподряд «Открытые решения». Аналитики данных для проекта отчётности. Опыт бизнес-аналитика "
    "от 3 лет, Power BI и SQL, сбор требований, моделирование данных, опыт в ритейле."
)
MAGNIT_SUB_BSS = (
    "Субподряд «БСС инжиниринг». Нужны аналитики данных для проекта отчётности: опыт "
    "бизнес-аналитиком 3+ года, стек Power BI, SQL, сбор и анализ требований, ритейл."
)
MOSBIRJA_1C = (
    "Аналитик 1С:ERP для производственного клиента. Опыт внедрения 1С:ERP от 5 лет, "
    "компетенции - анализ процессов производства, ТЗ, тестирование."
)


def test_tokens_normalizes_case_and_drops_short_words():
    assert tokens("Аналитик CRM 3.0 - внутренний проект")[:4] == ["аналитик", "crm", "внутренний", "проект"]
    assert "и" not in tokens("опыт и навыки")
    assert tokens(None) == []


def test_self_match_is_full():
    assert match_percent(MAGNIT, MAGNIT) == 100


def test_owner_samples_are_duplicates_above_threshold():
    # Субподрядные карточки повторяют описание прямой потребности (образцы владельца).
    assert match_percent(MAGNIT, MAGNIT_SUB_OPEN) >= 15
    assert match_percent(MAGNIT, MAGNIT_SUB_BSS) >= 15


def test_different_needs_stay_below_threshold():
    assert match_percent(MAGNIT, MOSBIRJA_1C) < 15


def test_empty_texts_match_zero():
    assert match_percent(None, MAGNIT) == 0
    assert match_percent("", "  ") == 0


def test_sequential_order_matters_more_than_scattered():
    # Последовательное изложение: тот же набор слов в том же порядке - выше процента.
    ordered = "аналитик данных power bi sql опыт требования"
    scattered = "sql bi требования power аналитик данных опыт"
    assert match_percent(ordered, ordered) >= match_percent(ordered, scattered)


def test_canonical_pair_orders_keys():
    assert canonical_pair("ANALYTICS-9", "ANALYTICS-5") == ("ANALYTICS-5", "ANALYTICS-9")
    assert canonical_pair("ANALYTICS-5", "ANALYTICS-5") == ("ANALYTICS-5", "ANALYTICS-5")
