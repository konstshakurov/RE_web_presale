"""Приёмочная проверка FR-023 (KON-33): набор вымышленных потребностей и CV с ручным списком
запрещённых строк (qa/testdata/fr023_anonymization_set.json).

Проверяет конвейер обезличивания как есть (словари + NER natasha), без сети и без БД.
Известные дефекты помечены xfail(strict=True): тест сам сообщит, когда Dev их исправит.
Критерии FR-023: (1) нулевая утечка на 5-10 примерах; контроль чрезмерного маскирования - ``keep``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.llm_gateway.anonymize import Anonymizer, Dictionaries

# QA_FR023_SET - путь к набору с реальными обезличенными примерами (вне репозитория)
SET_PATH = Path(os.environ.get("QA_FR023_SET") or (
    Path(__file__).resolve().parents[3] / "qa" / "testdata" / "fr023_anonymization_set.json"))
EXAMPLES = json.loads(SET_PATH.read_text(encoding="utf-8"))["examples"]

# id примера -> номер дефекта в Linear (xfail до исправления)
LEAK_BUGS: dict[str, str] = {}
KEEP_BUGS: dict[str, str] = {}

natasha = pytest.importorskip("natasha")


@pytest.fixture(scope="module")
def ner():
    from app.llm_gateway.ner import NatashaNer

    return NatashaNer()


def _dicts(ex: dict) -> Dictionaries:
    return Dictionaries(**{k: tuple(v) for k, v in ex["dictionary"].items()})


def _params(bugs: dict[str, str], key: str):
    out = []
    for ex in EXAMPLES:
        if not ex.get(key):
            continue
        marks = []
        if ex["id"] in bugs:
            marks.append(pytest.mark.xfail(strict=True, reason=bugs[ex["id"]]))
        out.append(pytest.param(ex, id=ex["id"], marks=marks))
    return out


def test_dataset_is_5_to_10_examples_with_both_kinds():
    assert 5 <= len(EXAMPLES) <= 10
    assert {e["kind"] for e in EXAMPLES} == {"need", "cv"}
    assert sum(1 for e in EXAMPLES if e["forbidden"]) >= 5


@pytest.mark.parametrize("ex", _params(LEAK_BUGS, "forbidden"))
def test_zero_leaks_with_dictionaries_and_ner(ex, ner):
    out = Anonymizer(_dicts(ex), ner).anonymize(ex["text"]).text.lower()
    leaked = [s for s in ex["forbidden"] if s.lower() in out]
    assert leaked == [], f"утечка в исходящем тексте: {leaked}"


@pytest.mark.parametrize("secret", [
    "a.korneev@nordwind-systems.test", "+7 (913) 555-01-02", "8 495 555 03 04", "89135550607",
    "https://wiki.tundra-bank.test/display/POL/requirements?id=77", "www.alpha.example.test",
])
def test_contacts_are_masked_without_any_dictionary(secret):
    """E-mail, телефоны (разные записи) и ссылки закрыты regex независимо от словарей и NER."""
    out = Anonymizer(Dictionaries()).anonymize(f"Контакт: {secret}, спасибо.").text
    assert secret not in out and "[" in out


@pytest.mark.parametrize("ex", _params(KEEP_BUGS, "keep"))
def test_technical_terms_are_not_masked(ex, ner):
    out = Anonymizer(_dicts(ex), ner).anonymize(ex["text"]).text.lower()
    lost = [s for s in ex["keep"] if s.lower() not in out]
    assert lost == [], f"маскируются технические термины: {lost}"


def test_non_pd_example_is_unchanged_by_dictionaries():
    ex = next(e for e in EXAMPLES if e["id"] == "FR023-07")
    r = Anonymizer(_dicts(ex)).anonymize(ex["text"])
    assert r.text == ex["text"] and r.mapping == {}


def test_ner_only_recall_is_reported_not_enforced(ner, capsys):
    """Строки, которых нет в словарях: ловит только NER. Порог мягкий (>= 50%), цифра - в отчёт."""
    ex = next(e for e in EXAMPLES if e["id"] == "FR023-09")
    out = Anonymizer(_dicts(ex), ner).anonymize(ex["text"]).text.lower()
    caught = [s for s in ex["ner_only"] if s.lower() not in out]
    ratio = len(caught) / len(ex["ner_only"])
    print(f"NER-only recall: {len(caught)}/{len(ex['ner_only'])}")
    assert ratio >= 0.5
    # контакты (e-mail) закрыты независимо от словарей и NER
    assert all(s.lower() not in out for s in ex["forbidden"])
