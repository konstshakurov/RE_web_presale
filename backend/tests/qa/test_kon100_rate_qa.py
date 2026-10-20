"""QA KON-101 (Pre.QA): приёмка KON-100 - запись ставки при назначении (CR-008), недостающие проверки.

Дополняет ``tests/unit/test_assign.py`` и ``tests/test_jira_integration.py`` (там покрыты формат тела,
«200 без записи», пустая ставка, «Клиент», частичный успех, 801 без ставки, 401/403/503).
Здесь - граничные значения, порядок шагов, идемпотентность повтора, секреты в предупреждениях и логах,
валидация на границе API. Сеть и БД не нужны; токены и пароли - FAKE_.

Матрица: FR-018 (формат, перечитывание, повтор), FR-008 (порядок: ставка после назначения),
FR-029 (введённое не теряется, секреты не в тексте отказа), NFR-006 (секреты не в логах).
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal

import pytest

from app.assign.service import AssignError, AssignInput
from app.integrations.jira.errors import JiraIntegrationError

from ..test_jira_integration import DEMAND_KEY, RATE_KW, _posts, _rate_adapter, _rate_issue
from ..unit.test_assign import CLIENT, EMP, KEY, PROD, USER, FakeJira, _api, issue, svc

FAKE_COOKIE = "FAKE_JSESSIONID_QA101_abcdef123456"
FAKE_PW = "FAKE_pw_QA101_zzzzzz"


# --- FR-008/FR-018: порядок шагов -----------------------------------------------------------

def test_rate_is_not_attempted_when_assignment_transition_fails():
    """Ставка пишется ПОСЛЕ успешного назначения: сорвался переход 21 - set_rate не вызывается."""
    j = FakeJira(**PROD)
    j.tr_errors = [JiraIntegrationError("Переход запрещён", code="jira_error", status=400)]
    with pytest.raises(AssignError) as e:
        svc(j).assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("2700")))
    assert "set_rate" not in j.names()
    assert e.value.step == "transition" and "rate" not in e.value.completed


def test_rate_written_exactly_once_and_last():
    j = FakeJira(**PROD)
    r = svc(j).assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("2700.50"), comment="к"))
    names = j.names()
    assert names.count("set_rate") == 1
    assert names.index("set_rate") > max(i for i, n in enumerate(names) if n == "do_transition")
    sent = next(c for c in j.calls if c[0] == "set_rate")
    # (имя, amount, vat, currency, unit, proposer, username): токен фейк в calls не пишет
    assert sent[1:] == ("2700.50", "20999", "21001", "21006", "22412", USER)
    assert r.steps[-1] == "rate"


def test_retry_with_rate_already_done_does_not_write_again():
    """Повтор с completed, содержащим rate, не шлёт ставку второй раз (идемпотентность)."""
    j = FakeJira(status="Кандидат назначен", transitions=[], **PROD)
    r = svc(j).assign(USER, KEY, AssignInput(
        EMP, rate_rub=Decimal("2700"), completed=("fields", "attach:1", "transition", "rate")))
    assert "set_rate" not in j.names() and not r.rate_pending


def test_retry_after_partial_with_changed_rate_sends_new_value():
    """Оператор поправил ставку после частичного успеха: уходит новое значение, остальное не дублируется."""
    j = FakeJira(**PROD)
    j.fail["set_rate"] = JiraIntegrationError("не записана", code="jira_error")
    s = svc(j)
    r = s.assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("2700")))
    assert r.rate_pending
    j.fail.clear()
    j.calls.clear()
    j.status = "Кандидат назначен"
    j.transitions = []
    s.assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("2800"), completed=r.steps))
    assert [c[1] for c in j.calls if c[0] == "set_rate"] == ["2800"]
    assert not {"attach_file", "do_transition", "set_fields", "add_comment"} & set(j.names())


# --- FR-018 БП-2: граничные состояния «Кто предлагает ставку» ------------------------------------

def test_unknown_proposer_rate_is_neither_required_nor_sent():
    """«Кто предлагает ставку» не задан в Jira: ставка не обязательна и не отправляется.

    Фиксируем текущее поведение; спецификация (FR-018) этот случай не описывает - вопрос аналитику.
    """
    j = FakeJira()                                   # без customfield_21105
    r = svc(j).assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("3000")))
    assert "set_rate" not in j.names() and "rate" not in r.steps and not r.rate_pending


@pytest.mark.parametrize("rate", [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_service_rejects_non_positive_or_non_finite_rate_before_any_write(rate):
    j = FakeJira(**PROD)
    with pytest.raises(AssignError) as e:
        svc(j).assign(USER, KEY, AssignInput(EMP, rate_rub=rate))
    assert e.value.code == "bad_request"
    assert not {"set_fields", "attach_file", "do_transition", "set_rate"} & set(j.names())


# --- API: граница валидации --------------------------------------------------------------------

@pytest.mark.parametrize("bad", [0, -5, "abc", "", [1], {"a": 1}])
def test_api_rejects_bad_rate_without_calling_jira(bad):
    j = FakeJira(**PROD)
    client, headers, _ = _api(j)
    r = client.post(f"/needs/{KEY}/assign", headers=headers, json={"employee_key": EMP, "rate_rub": bad})
    assert r.status_code == 422, r.text
    assert not {"set_fields", "attach_file", "do_transition", "set_rate"} & set(j.names())


@pytest.mark.xfail(strict=True, reason="BUG KON-103: у ставки нет верхней границы; 1e999999 доходит до set_rate")
def test_api_rejects_absurd_rate_magnitude_without_calling_jira():
    j = FakeJira(**PROD)
    client, headers, _ = _api(j)
    r = client.post(f"/needs/{KEY}/assign", headers=headers, json={"employee_key": EMP, "rate_rub": "1e999999"})
    assert r.status_code == 422 and "set_rate" not in j.names()


def test_api_client_proposer_ignores_supplied_rate_and_reports_no_rate_step():
    """Клиент прислал rate_rub вручную (в обход UI): backend её не отправляет в Jira."""
    j = FakeJira(**CLIENT)
    client, headers, _ = _api(j)
    r = client.post(f"/needs/{KEY}/assign", headers=headers,
                    json={"employee_key": EMP, "rate_rub": 999})
    assert r.status_code == 200 and "rate" not in r.json()["steps"] and not r.json()["rate_pending"]
    assert "set_rate" not in j.names()


def test_api_status_endpoint_has_no_rate_input():
    """POST /needs/{key}/status не принимает ставку (откат CR-007): лишнее поле игнорируется, в Jira не уходит."""
    j = FakeJira(**PROD)
    client, headers, _ = _api(j)
    r = client.post(f"/needs/{KEY}/status", headers=headers,
                    json={"action": "hire", "fields": {"customfield_20809": "5000"}, "rate_rub": 5000})
    body = r.status_code, r.text
    tr = [c for c in j.calls if c[0] == "do_transition"]
    assert r.status_code in (200, 422), body
    if tr:
        assert "customfield_20809" not in tr[0][2]


# --- FR-029 / NFR-006: секреты ------------------------------------------------------------------

def test_partial_success_warning_and_logs_do_not_leak_secrets(caplog):
    j = FakeJira(**PROD)
    j.fail["set_rate"] = JiraIntegrationError(
        f"отказ Jira; Cookie: JSESSIONID={FAKE_COOKIE}; password={FAKE_PW}", code="jira_error", status=500)
    with caplog.at_level(logging.DEBUG):
        r = svc(j).assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("2700")))
    text = " ".join(r.warnings) + " " + "\n".join(rec.getMessage() for rec in caplog.records)
    assert r.rate_pending
    assert FAKE_COOKIE not in text and FAKE_PW not in text


def test_api_error_body_does_not_leak_secrets_on_rate_validation_failure():
    j = FakeJira(**PROD)
    client, headers, _ = _api(j)
    r = client.post(f"/needs/{KEY}/assign", headers=headers, json={"employee_key": EMP})
    assert r.status_code == 422
    assert "FAKE_" not in r.text.replace("FAKE_csrf", "")


# --- Адаптер: форматы суммы и перечитывание -----------------------------------------------------

@pytest.mark.parametrize("given,sent", [
    (2700, "2700"), ("2700", "2700"), (Decimal("3800.00"), "3800"), ("2700,5", "2700.5"),
    ("1e3", "1000"), (0.5, "0.5"),
])
def test_amount_is_serialized_as_plain_number_string(adapter_factory, given, sent):
    adapter, session, handler = _rate_adapter(adapter_factory, issue=_rate_issue(amount=float(sent)))
    adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **{**RATE_KW, "amount": given})
    assert json.loads(_posts(handler)[0].content)["customfield_20809"] == sent


@pytest.mark.parametrize("amount", ["2 700", "Infinity", "-0.01", "1,2,3", "٣٠٠٠x", True])
def test_amount_garbage_is_not_sent(adapter_factory, amount):
    adapter, session, handler = _rate_adapter(adapter_factory)
    with pytest.raises(JiraIntegrationError):
        adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **{**RATE_KW, "amount": amount})
    assert _posts(handler) == []


def test_reread_accepts_amount_returned_as_string(adapter_factory):
    """Jira может отдать число строкой: сверка по Decimal, а не по типу."""
    adapter, session, _ = _rate_adapter(adapter_factory, issue=_rate_issue(amount="2700.0"))
    adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **RATE_KW)


def test_reread_without_option_ids_checks_amount_only(adapter_factory):
    """Если Jira не вернула id справочников в перечитывании, сверка идёт по сумме (текущее поведение)."""
    from ..conftest import response
    from ..test_jira_integration import ISSUE_PATH, RATE_PATH, MYSELF_OK, login_routes, router
    handler = router(default=MYSELF_OK, **{
        RATE_PATH: lambda: response(None, 200),
        ISSUE_PATH: lambda: response({"key": DEMAND_KEY, "fields": {"customfield_20809": 2700.0}}),
        **login_routes()})
    adapter = adapter_factory(handler)
    session = adapter.login("probe.user", "FAKE_pw")
    adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **RATE_KW)


def test_known_limitation_rewriting_same_value_cannot_detect_silent_200(adapter_factory):
    """ОГРАНИЧЕНИЕ (не дефект, фиксируем): если в Jira уже стоит та же ставка, молчаливое «200 без
    записи» перечитыванием неотличимо от успеха. Безвредно (значение уже верное), но проверка
    «записали именно мы» в этом случае не работает.
    """
    adapter, session, handler = _rate_adapter(adapter_factory, issue=_rate_issue(amount=2700.0))
    adapter.set_rate(session.sealed_token, DEMAND_KEY, username="o", **RATE_KW)   # не падает
    assert len(_posts(handler)) == 1


def test_issue_helper_is_importable():
    """Страховка от поломки общих фейков: issue() из unit-тестов возвращает ожидаемую форму."""
    assert issue("ANALYTICS-1", "Поиск кандидата")["key"] == "ANALYTICS-1"
