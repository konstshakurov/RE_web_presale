"""Чистая логика matching (KON-38): разбор ответов LLM, запросы, отбор, пакеты, профиль."""
from __future__ import annotations

import json

import pytest

from app.matching.profile import guess_years, parse_sections
from app.matching.prompts import (
    MATCH_CV_CHARS,
    MAX_TAGS,
    Candidate,
    NeedText,
    ResponseError,
    build_match_prompt,
    build_skills_prompt,
    normalize_tag,
    parse_match,
    parse_skills,
)
from app.matching.selection import count_hits, need_text, preselect, split_batches

LABELS = {"K1": "EMP-1", "K2": "EMP-2", "K3": "EMP-3"}


# --- навыки ---

def test_parse_skills_array_normalizes_and_dedupes():
    got = parse_skills('["SQL", " Rest API ", "sql", "BPMN.", "", 5, null]')
    assert got == ["sql", "rest api", "bpmn"]


def test_parse_skills_from_object_think_block_and_fence():
    raw = '<think>подумаю</think>\n```json\n{"tech": ["Kafka", "SQL"], "tools": ["Jira"]}\n```'
    assert parse_skills(raw) == ["kafka", "sql", "jira"]


def test_parse_skills_json_inside_text():
    assert parse_skills('Вот список: ["UML", "Confluence"] Удачи!') == ["uml", "confluence"]


def test_parse_skills_caps_count_and_rejects_empty():
    many = json.dumps([f"tag{i}" for i in range(MAX_TAGS + 20)])
    assert len(parse_skills(many)) == MAX_TAGS
    for bad in ("", "не json", "[]", '{"a": 1}', "[1, 2]"):
        with pytest.raises(ResponseError):
            parse_skills(bad)


def test_normalize_tag_limits():
    assert normalize_tag("  Системный   Анализ ") == "системный анализ"
    assert normalize_tag("x" * 129) is None
    assert normalize_tag(None) is None


def test_skills_prompt_clips_long_cv_and_marks_data():
    p = build_skills_prompt("а" * 50_000)
    assert p.count("а") < 21_000 and "сокращён" in p and p.startswith("Резюме:")


# --- сопоставление ---

def test_parse_match_basic_clamps_and_aliases():
    raw = json.dumps([
        {"id": "K1", "percent": 87, "rationale": "Много  опыта\nв BPMN"},
        {"id": "k2", "match": "130%", "reason": "ок"},
        {"id": "K3", "score": -5},
        {"id": "K9", "percent": 50},      # неизвестная метка
        {"id": "K1", "percent": 1},       # повтор
        {"percent": 10},                  # без метки
    ])
    got = parse_match(raw, LABELS)
    assert set(got) == {"EMP-1", "EMP-2", "EMP-3"}
    assert got["EMP-1"].percent == 87 and got["EMP-1"].rationale == "Много опыта в BPMN"
    assert got["EMP-2"].percent == 100
    assert got["EMP-3"].percent == 0 and got["EMP-3"].rationale


def test_parse_match_object_wrapper_and_float():
    raw = '{"results": [{"id": "K1", "percent": 72.6, "rationale": "x"}]}'
    assert parse_match(raw, LABELS)["EMP-1"].percent == 73


def test_parse_match_errors():
    for bad in ("", "текст", "[]", '[{"id": "K9", "percent": 5}]', '[{"id": "K1"}]', "42"):
        with pytest.raises(ResponseError):
            parse_match(bad, LABELS)


def test_rationale_is_truncated():
    raw = json.dumps([{"id": "K1", "percent": 1, "rationale": "я" * 5000}])
    assert len(parse_match(raw, LABELS)["EMP-1"].rationale) == 1000


def test_match_prompt_has_no_keys_or_names_and_clips_cv():
    need = NeedText("Аналитик в банк", "Описание", "Опыт 3 года")
    cands = [
        Candidate("K1", "EMP-77", ("sql", "bpmn"), "Текст " * 5000, "не подключать к клиенту X",
                  "Доп. опыт"),
        Candidate("K2", "EMP-78", (), "Короткое CV", None, None),
    ]
    p = build_match_prompt(need, cands)
    assert "EMP-77" not in p and "EMP-78" not in p
    assert "### K1" in p and "### K2" in p and "не подключать к клиенту X" in p
    assert "sql, bpmn" in p and "сокращён" in p
    assert len(p) < 2 * MATCH_CV_CHARS + 3000


# --- отбор и пакеты ---

def test_count_hits_word_boundaries():
    text = need_text("Нужен SQL и Rest API", "Работа с c# и 1с", None)
    assert count_hits(["sql", "rest api", "c#", "java"], text) == 3
    assert count_hits(["sq"], text) == 0       # подстрока слова не считается
    assert count_hits(["1с"], text) == 1


def test_preselect_keeps_all_under_limit_and_orders_by_hits():
    cands = [("E1", "Борис", ["java"]), ("E2", "Анна", ["sql", "bpmn"]), ("E3", "Виктор", ["sql"])]
    got = preselect(cands, "sql bpmn", limit=10)
    assert [s.key for s in got] == ["E2", "E3", "E1"]
    assert [s.hits for s in got] == [2, 1, 0]


def test_preselect_limit_takes_best_and_is_stable():
    cands = [(f"E{i}", f"Имя{i:02d}", ["sql"] if i % 2 else []) for i in range(10)]
    got = preselect(cands, "sql", limit=3)
    assert len(got) == 3 and all(s.hits == 1 for s in got)
    assert [s.key for s in got] == ["E1", "E3", "E5"]


def test_split_batches_by_count_and_chars():
    assert split_batches([10] * 7, max_items=3, max_chars=1000) == [[0, 1, 2], [3, 4, 5], [6]]
    assert split_batches([600, 600, 100], max_items=5, max_chars=1000) == [[0], [1, 2]]
    assert split_batches([5000], max_items=5, max_chars=1000) == [[0]]
    assert split_batches([], max_items=5, max_chars=10) == []


# --- профиль ---

CV = """Иван Тестов
Профиль:
Системный аналитик, опыт работы 7 лет.
Основной стек
SQL, BPMN, REST
Отрасли
Банки; ритейл
Ключевые навыки
Требования
Проектирование API
Языки
Английский B2
Проекты
Проект А - аналитик, 2022-2024
"""


def test_parse_sections_and_years():
    s = parse_sections(CV)
    assert s["stack"] == "SQL, BPMN, REST"
    assert s["industries"] == "Банки; ритейл"
    assert s["skills"] == "Требования\nПроектирование API"
    assert s["languages"] == "Английский B2"
    assert s["projects"].startswith("Проект А")
    assert "Системный аналитик" in s["profile"]
    assert guess_years(CV) == 7


def test_profile_without_headings_is_empty_and_years_not_invented():
    assert parse_sections("Просто текст резюме без заголовков") == {}
    assert parse_sections(None) == {}
    assert guess_years("Работал давно") is None
