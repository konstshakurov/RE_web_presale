"""CR-019 (FR-005 v1.1, FR-036 v1.2): правила объективности вывода рекомендации LLM.
Промпт содержит запреты, санитайзер нормализует формулировки и отсекает домыслы."""
from __future__ import annotations

import json

from app.matching.prompts import (
    GAPS_INSTRUCTION,
    MATCH_SYSTEM,
    objectify_output,
    parse_match,
)

LABELS = {"K1": "EMP-1"}


def test_prompts_state_objectivity_rules():
    """Промпт содержит запреты правил CR-019 (только по потребности; формулировки
    «в CV не указан»; согласованность; без модальностей домысла)."""
    for system in (MATCH_SYSTEM, GAPS_INSTRUCTION):
        assert "не сформулировано" in system or "не сказано явно" in system
        assert "не указан" in system
        assert "собственн" in system
    assert "согласован" in MATCH_SYSTEM
    assert "без модальностей" in MATCH_SYSTEM.lower()


def test_no_experience_becomes_not_mentioned_c_v():
    """«Нет опыта» / «Отсутствует опыт» -> «Не указан опыт» (правило 2)."""
    r, _ = objectify_output("Отсутствует опыт работы с Redis", ())
    assert r == "Не указан опыт работы с Redis"
    r, _ = objectify_output("Нет опыта работы с Redis", ())
    assert r == "Не указан опыт работы с Redis"
    r, _ = objectify_output(
        "Сильный профиль. Отсутствует явный опыт управления командой аналитиков.", ()
    )
    assert "Не указан опыт" in r
    assert "Отсутств" not in r
    assert "Нет опыта" not in r


def test_no_mention_becomes_not_mentioned_c_v():
    """«Нет упоминания» -> «Не упомнут …» (правило 2 + краткость, владелец 09.11)."""
    r, _ = objectify_output("Нет упоминания опыта разбора инцидентов", ())
    assert r == "Не упомнут опыта разбора инцидентов"
    _, g = objectify_output("ok", ("Нет упоминания опыта проектирования и работы с Redis",))
    assert g == ("Не упомнут опыта проектирования и работы с Redis",)


def test_drop_selfmade_threshold_clause_keeps_useful_part():
    """Модальность домысла отсекается от запятой-конъюнкции; полезная часть сохранена."""
    rationale = (
        "Кандидат полностью закрывает требования к сбору и документированию требований, "
        "моделированию (BPMN, UML), интеграциям (REST, Kafka) и имеет подтверждённый опыт "
        "руководства группой аналитиков. Недостаток отражен по узкоспециализированным "
        "инструментам и паттернам, а также 4-летний стаж в роли системного аналитика может "
        "считаться нижним порогом для позиции Senior."
    )
    r, _ = objectify_output(rationale, ())
    assert "может считаться" not in r
    assert "нижним порогом" not in r
    assert "Недостаток отражен по узкоспециализированным инструментам и паттернам." in r


def test_drop_selfmade_gap_item():
    """Пункт gaps целиком с модальностью домысла удаляется (правило 4)."""
    _, g = objectify_output(
        "Сильный профиль с прямым опытом руководства командой аналитиков.",
        (
            "Отсутствует опыт работы с инструментом Conductor или аналогами",
            "Опыт работы 5 лет может не соответствовать запросу на позицию Senior",
            "Не указан опыт участия в разборе инцидентов",
        ),
    )
    assert g[0] == "Не указан опыт работы с инструментом Conductor или аналогами"
    assert len(g) == 2  # пункт с модальностью удалён
    assert g[1] == "Не указан опыт участия в разборе инцидентов"  # уже объективная формулировка


def test_parse_match_applies_objectivity():
    """Санитайзер встроен в единый разбор ответа (глубокая оценка/поиск/скрининг)."""
    raw = json.dumps([
        {"id": "K1", "percent": 85,
         "rationale": "Полный профиль. Нет опыта работы с Redis.",
         "gaps": ["Отсутствует опыт работы с Redis"]},
    ])
    res = parse_match(raw, LABELS)
    assert res["EMP-1"].rationale.endswith("Не указан опыт работы с Redis.")
    assert res["EMP-1"].gaps == ("Не указан опыт работы с Redis",)


def test_clean_output_for_already_compliant_text():
    """Правильный текст проходит без изменений (без лишних правок)."""
    r, g = objectify_output(
        "Профиль закрывает требования по BPMN, UML и REST; два пункта ограничены.",
        ("Не указан опыт работы с Conductor",),
    )
    assert r.startswith("Профиль закрывает")
    assert g == ("Не указан опыт работы с Conductor",)
