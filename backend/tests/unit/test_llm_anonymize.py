"""Конвейер обезличивания (FR-023, критерий 1): regex, словари, NER, пересечения, обратная
подстановка, проверка утечек. Строки в тестах заведомо тестовые (не данные клиентов)."""
from __future__ import annotations

import pytest

from app.llm_gateway.anonymize import (
    COMPANY,
    LOCATION,
    PERSON,
    Anonymizer,
    Dictionaries,
    restore,
)

from ._llm_fakes import FakeNer

PERSONS = ("Тестов Тест Тестович", "Образцова Анна Образцовна", "Пробин Пётр")
COMPANIES = ("Тестовая Компания Альфа", "Бета-Тест Групп")
PROJECTS = ("Омега-Тест", "Проект Гамма-Образец")
LOCATIONS = ("Тестоград",)
D = Dictionaries(persons=PERSONS, companies=COMPANIES, projects=PROJECTS, locations=LOCATIONS)

# (текст, запрещённые к отправке строки): набор из 8 примеров потребностей и CV
EXAMPLES = [
    ("Клиент Тестовая Компания Альфа ищет аналитика на проект Омега-Тест, офис в Тестоград.",
     ["Тестовая Компания Альфа", "Омега-Тест", "Тестоград"]),
    ("Резюме: Тестов Тест Тестович, тел. +7 (900) 111-22-33, почта tester@example.test.",
     ["Тестов", "Тест Тестович", "+7 (900) 111-22-33", "111-22-33", "tester@example.test"]),
    ("Контакт: 8 900 111 22 33, сайт https://alpha.example.test/team?id=7, повтор 89001112233.",
     ["900 111 22 33", "alpha.example.test", "89001112233"]),
    ("Работал в Бета-Тест Групп над проектом Гамма-Образец; руководитель Пробин Пётр.",
     ["Бета-Тест Групп", "Гамма-Образец", "Пробин"]),
    ("Передать Образцовой Анне. Спросить у Пробина о ставке.",
     ["Образцов", "Пробин"]),
    ("Участвовал в «Омега-Тест» (2022-2024), затем в ОМЕГА-ТЕСТ v2.",
     ["Омега-Тест", "ОМЕГА-ТЕСТ"]),
    ("Python, PostgreSQL, опыт 5 лет. Требуется удалённая работа.", []),
    ("Почта hr@alpha.example.test; резервная hr2@alpha.example.test; www.alpha.example.test.",
     ["hr@alpha.example.test", "hr2@alpha.example.test", "www.alpha.example.test"]),
]


@pytest.mark.parametrize("text, forbidden", EXAMPLES)
def test_zero_leaks_on_examples(text, forbidden):
    a = Anonymizer(D)
    out = a.anonymize(text).text
    for s in forbidden:
        assert s.lower() not in out.lower(), f"утечка {s!r} в {out!r}"
    assert a.find_leaks(out) == []


def test_non_pd_text_unchanged():
    a = Anonymizer(D)
    text = "Python, PostgreSQL, опыт 5 лет."
    r = a.anonymize(text)
    assert r.text == text and r.mapping == {} and r.replacements == 0


def test_same_value_same_placeholder_and_mapping():
    r = Anonymizer(D).anonymize(
        "Омега-Тест и снова омега-тест; Бета-Тест Групп; mail a@x.test, a@x.test")
    assert r.text.count("[ПРОЕКТ_1]") == 2
    assert "[КОМПАНИЯ_1]" in r.text and r.text.count("[EMAIL_1]") == 2
    assert r.mapping["[ПРОЕКТ_1]"] == "Омега-Тест"
    assert r.counts == {"project": 1, "company": 1, "email": 1}


def test_person_forms_share_one_placeholder():
    r = Anonymizer(D).anonymize("Пробин Пётр пришёл. Позвонить Пробину. Пётр согласен.")
    assert r.text.count("[ПЕРСОНА_1]") == 3 and "[ПЕРСОНА_2]" not in r.text
    assert r.mapping == {"[ПЕРСОНА_1]": "Пробин Пётр"}


def test_word_boundaries_no_partial_matches():
    d = Dictionaries(companies=("Альфа",))
    r = Anonymizer(d).anonymize("Альфа и Альфабет, но не АльфаБанк.")
    assert r.text == "[КОМПАНИЯ_1] и Альфабет, но не АльфаБанк."


def test_short_terms_ignored():
    d = Dictionaries(companies=("ИТ", "Я"), projects=("X1",))
    assert Anonymizer(d).anonymize("ИТ и Я и X1").text == "ИТ и Я и X1"


def test_longer_term_wins_over_nested():
    d = Dictionaries(companies=("Тест Групп",), projects=("Тест Групп Омега",))
    r = Anonymizer(d).anonymize("Работа в Тест Групп Омега.")
    assert r.text == "Работа в [ПРОЕКТ_1]."


def test_email_inside_url_and_trailing_punctuation():
    r = Anonymizer(Dictionaries()).anonymize(
        "См. https://a.example.test/x, потом b@a.example.test.")
    assert r.text == "См. [ССЫЛКА_1], потом [EMAIL_1]."


def test_ner_adds_unknown_names_and_dictionary_has_priority():
    ner = FakeNer([("Неизвестный Человек", PERSON), ("Москва", LOCATION),
                   ("Омега-Тест", COMPANY), ("ТестОрг", COMPANY)])
    r = Anonymizer(D, ner).anonymize("Неизвестный Человек из Москва, Омега-Тест, ТестОрг.")
    assert r.ner_used
    assert "Неизвестный" not in r.text and "Москва" not in r.text and "ТестОрг" not in r.text
    # словарь «проект» сильнее NER «компания» для той же строки
    assert r.mapping["[ПРОЕКТ_1]"] == "Омега-Тест"


def test_ner_absent_flag():
    assert Anonymizer(D).anonymize("текст").ner_used is False


def test_find_leaks_ignores_placeholder_labels():
    d = Dictionaries(projects=("ПРОЕКТ Икс",), companies=("КОМПАНИЯ",))
    a = Anonymizer(d)
    assert a.find_leaks("Метки [КОМПАНИЯ_1] и [ПРОЕКТ_2] допустимы") == []
    assert a.find_leaks("а вот ПРОЕКТ Икс остался") == ["ПРОЕКТ Икс"]


def test_restore_exact_and_tolerant():
    mapping = {"[ПЕРСОНА_1]": "Пробин Пётр", "[КОМПАНИЯ_1]": "Бета-Тест Групп",
               "[EMAIL_1]": "a@x.test"}
    assert restore("[ПЕРСОНА_1] работает в [КОМПАНИЯ_1].", mapping) == \
        "Пробин Пётр работает в Бета-Тест Групп."
    # LLM потеряла скобки / изменила регистр / заменила _ на пробел
    assert restore("ПЕРСОНА_1 и [персона 1], писать на [EMAIL_1]", mapping) == \
        "Пробин Пётр и Пробин Пётр, писать на a@x.test"
    # неизвестный номер остаётся как есть
    assert restore("[ПЕРСОНА_7]", mapping) == "[ПЕРСОНА_7]"
    assert restore("текст", {}) == "текст"


def test_round_trip_restores_original_entities():
    text = "Клиент Тестовая Компания Альфа, проект Омега-Тест, написать a@x.test"
    a = Anonymizer(D).anonymize(text)
    assert restore(a.text, a.mapping) == text


def test_dictionaries_merge_dedup_case_insensitive():
    m = Dictionaries(companies=("Альфа",)).merged(Dictionaries(companies=("альфа", "Бета")))
    assert m.companies == ("Альфа", "Бета")


def test_large_dictionary_is_fast():
    import time

    d = Dictionaries(persons=tuple(f"Фамилия{i}ов Имя{i}ей" for i in range(3000)))
    a = Anonymizer(d)
    t = time.monotonic()
    a.anonymize("Текст " * 2000 + "Фамилия77ов Имя77ей")
    assert time.monotonic() - t < 5


def test_legal_form_and_quotes_variants():
    d = Dictionaries(companies=("ООО «Тестовая Компания Бета»",), projects=("Проект Зета-Тест",))
    r = Anonymizer(d).anonymize(
        "ООО «Тестовая Компания Бета», или просто Тестовая Компания Бета; "
        "Зета-Тест и Проект Зета-Тест")
    assert "Бета" not in r.text and "Зета" not in r.text
    assert r.mapping["[КОМПАНИЯ_1]"] == "ООО «Тестовая Компания Бета»"
    assert r.counts == {"company": 1, "project": 1}


def test_natasha_smoke_if_installed():
    """Реальный NER: находит неизвестное ФИО; словарей нет вовсе."""
    pytest.importorskip("natasha")
    from app.llm_gateway.ner import NatashaNer

    r = Anonymizer(Dictionaries(), NatashaNer()).anonymize(
        "Кандидата зовут Иван Петров, он работал в Москве.")
    assert "Петров" not in r.text and "[ПЕРСОНА_1]" in r.text and r.ner_used
