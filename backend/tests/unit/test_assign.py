"""Назначение, статус-действия и комментарии в Jira (KON-43): сервис, API и мост к адаптеру.

Сеть и БД не нужны: Jira - фейк порта записи либо ``httpx.MockTransport``. Данные синтетические,
токены и пароли - FAKE_.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.assign.router import router as assign_router
from app.assign.service import (
    AssignError,
    AssignInput,
    AssignService,
)
from app.integrations.jira import (
    CODE_JIRA_UNAVAILABLE,
    CODE_NEEDS_REAUTHORIZATION,
    AllowedValue,
    TransitionField,
    TransitionView,
)
from app.integrations.jira.errors import JiraIntegrationError
from app.jira_session.service import JiraStatusInfo

from ..conftest import FAKE_RAW_TOKEN, response
from ._cv_fakes import make_client
from ._sync_fakes import issue

KEY = "ANALYTICS-7"
EMP = "EMP-1"
USER = "ivan"
FAKE_SEALED = "FAKE_SEALED_TOKEN"


def av(*pairs):
    return tuple(AllowedValue(i, n) for i, n in pairs)


REASON = TransitionField("customfield_16100", "Причина отсутствия кандидата", False,
                         av(("23300", "Требуется очное присутствие"), ("21300", "Низкая ставка")))
VAT = TransitionField("customfield_20810", "Включает НДС", True,
                      av(("20999", "Да"), ("21000", "Нет")))
CUR = TransitionField("customfield_20811", "Валюта", True,
                      av(("21001", "руб."), ("21002", "EUR")))
UNIT = TransitionField("customfield_20812", "Единица измерения", True, av(("21006", "ч (час)")))

T_ASSIGN = TransitionView("21", "Кандидат назначен", "Кандидат назначен")
T_NONE = TransitionView("171", "Кандидатов нет", "Кандидатов нет", (REASON,))
T_HIRE = TransitionView("801", "В найм", "Найм", (VAT, CUR, UNIT))
T_SEARCH = TransitionView("11", "Поиск", "Поиск кандидата")
T_NEED = TransitionView("181", "Требуется кандидат", "Требуется кандидат")  # не в белом списке


class FakeJira:
    def __init__(self, status="Поиск кандидата", transitions=None, **fields):
        self.fields = fields
        self.status = status
        self.transitions = list(transitions if transitions is not None else
                                [T_ASSIGN, T_NONE, T_HIRE, T_NEED])
        self.calls: list[tuple] = []
        self.fail: dict[str, Exception] = {}
        self.reread_fails = False
        self.tokens: set[str] = set()
        self.tr_errors: list[Exception | None] = []   # по вызову do_transition; None - успех
        self.set_errors: dict[str, Exception] = {}    # ключ - значение 11506, на котором падает PUT

    def _do(self, name, token, *args):
        self.tokens.add(token)
        self.calls.append((name, *args))
        if name in self.fail:
            raise self.fail[name]

    def get_issue(self, token, key, fields=None):
        self._do("get_issue", token, key)
        if fields == ["customfield_11506"]:
            self.calls.pop()
            self.calls.append(("get_need_cv_rule",))
            cv = self.fields.get("customfield_11506")
            return {"key": key, "fields": {"customfield_11506": cv}}
        if self.reread_fails and self.calls.count(("get_issue", key)) > 1:
            raise JiraIntegrationError("down", code=CODE_JIRA_UNAVAILABLE)
        return issue(key, self.status, **self.fields)

    def get_transitions(self, token, key):
        self._do("get_transitions", token, key)
        return self.transitions

    def do_transition(self, token, key, transition_id, *, fields, comment):
        self._do("do_transition", token, transition_id, dict(fields), comment)
        if self.tr_errors:
            err = self.tr_errors.pop(0)
            if err is not None:
                raise err
        self.status = next(t.to_status for t in self.transitions if t.id == transition_id)

    def set_fields(self, token, key, fields):
        self._do("set_fields", token, dict(fields))
        cv = fields.get("customfield_11506")
        if cv and cv["value"] in self.set_errors:
            raise self.set_errors[cv["value"]]
        if cv:
            self.fields["customfield_11506"] = cv

    def add_comment(self, token, key, body):
        self._do("add_comment", token, body)

    def attach_file(self, token, key, filename, content, mime_type):
        self._do("attach_file", token, filename, content)

    def set_rate(self, token, key, *, amount, vat, currency, unit, proposer, username=None):
        self._do("set_rate", token, str(amount), vat, currency, unit, proposer, username)

    def names(self):
        return [c[0] for c in self.calls]


class FakeEmployees:
    def __init__(self, login="emp.login"):
        self.login = login

    def get_profile(self, key):
        return (key == EMP, self.login if key == EMP else None)


def _file(fid, kind="main_cv", employee=EMP, archived=False):
    return SimpleNamespace(id=fid, employee_key=employee, kind=kind, is_archived=archived,
                           file_name=f"f{fid}.docx", mime_type="application/msword")


class FakeFiles:
    def __init__(self):
        self.rows = {1: _file(1), 2: _file(2, "extra_cv"), 3: _file(3, "materials"),
                     4: _file(4, "main_cv", archived=True), 5: _file(5, "materials", "EMP-2"),
                     6: _file(6, "client_template_cv")}

    def get_file(self, fid):
        return self.rows.get(fid)

    def get_content(self, fid):
        return (self.rows[fid], b"data%d" % fid) if fid in self.rows else None

    def current_main(self, key):
        return self.rows[1] if key == EMP else None


class FakeCache:
    def __init__(self):
        self.rows = []

    def upsert_needs(self, rows):
        self.rows += rows
        return len(rows)


def svc(jira, *, token=FAKE_SEALED, files=True, employees=True):
    cache = FakeCache()
    s = AssignService(jira, lambda login: token, FakeEmployees() if employees else None,
                      FakeFiles() if files else None, cache)
    s.cache = cache
    return s


# --- статус-действия: белый список ∩ переходы Jira ---

def test_actions_whitelist_intersects_jira_transitions():
    j = FakeJira(transitions=[T_ASSIGN, T_NONE, T_HIRE, T_NEED, T_SEARCH])
    out = svc(j).list_actions(USER, KEY)
    assert [a.action for a in out.actions] == ["search", "hire", "no_candidates"]
    assert out.status == "Поиск кандидата"
    # «Требуется кандидат» и «Кандидат назначен» кнопками не становятся
    assert "Требуется кандидат" not in [a.target_status for a in out.actions]
    # Jira не отдаёт переход - кнопки нет
    only = svc(FakeJira(transitions=[T_ASSIGN, T_NEED])).list_actions(USER, KEY)
    assert only.actions == ()


def test_actions_hire_prefilled_from_need_and_reasons_from_jira():
    j = FakeJira(customfield_20810={"value": "Да"}, customfield_20811={"value": "руб."},
                 customfield_20812={"value": "ч"}, customfield_20809=2125.0)
    out = svc(j).list_actions(USER, KEY)
    hire = next(a for a in out.actions if a.action == "hire")
    pre = {f.kind: f.prefill_id for f in hire.fields}
    assert pre == {"vat": "20999", "currency": "21001", "unit": "21006"}
    nc = next(a for a in out.actions if a.action == "no_candidates")
    assert nc.comment_required and nc.fields[0].kind == "reason"
    assert ("23300", "Требуется очное присутствие") in nc.fields[0].options


# --- смена статуса ---

def test_no_candidates_sends_reason_and_comment_via_transition():
    j = FakeJira()
    r = svc(j).change_status(USER, KEY, "no_candidates", comment="нет людей",
                             fields={"customfield_16100": "23300"})
    call = next(c for c in j.calls if c[0] == "do_transition")
    assert call[1:] == ("171", {"customfield_16100": {"id": "23300"}}, "нет людей")
    assert r.status == "Кандидатов нет" and r.refreshed


@pytest.mark.parametrize("comment,fields,text", [
    ("", {"customfield_16100": "23300"}, "комментар"),
    ("текст", {}, "причин"),
])
def test_no_candidates_requires_reason_and_comment_before_send(comment, fields, text):
    j = FakeJira()
    with pytest.raises(AssignError) as e:
        svc(j).change_status(USER, KEY, "no_candidates", comment=comment, fields=fields)
    assert e.value.code == "bad_request" and text in e.value.message.lower()
    assert "do_transition" not in j.names()


def test_hire_prefills_rate_and_checks_required_before_send():
    j = FakeJira(customfield_20809=2125.0, customfield_20810={"value": "Нет"},
                 customfield_20811={"value": "руб."},
                 customfield_20812={"value": "ч"})
    svc(j).change_status(USER, KEY, "hire")
    sent = next(c for c in j.calls if c[0] == "do_transition")
    assert sent[1] == "801" and sent[2] == {
        "customfield_20810": {"id": "21000"}, "customfield_20811": {"id": "21001"},
        "customfield_20812": {"id": "21006"}}
    assert sent[3]  # комментарий перехода передаётся всегда

    j2 = FakeJira()  # в потребности ставки нет, пользователь не задал
    with pytest.raises(AssignError) as e:
        svc(j2).change_status(USER, KEY, "hire", fields={"customfield_20810": "20999"})
    assert e.value.code == "bad_request" and "Валюта" in e.value.message
    assert "do_transition" not in j2.names()


def test_search_one_click_default_comment_and_unavailable():
    j = FakeJira(status="Требуется кандидат", transitions=[T_SEARCH, T_NEED])
    svc(j).change_status(USER, KEY, "search")
    assert next(c for c in j.calls if c[0] == "do_transition")[3]
    j2 = FakeJira(transitions=[T_NEED])
    with pytest.raises(AssignError) as e:
        svc(j2).change_status(USER, KEY, "search")
    assert e.value.code == "transition_unavailable"
    with pytest.raises(AssignError) as e:  # «Кандидат назначен» не статус-действие
        svc(j).change_status(USER, KEY, "assigned")
    assert e.value.code == "bad_request"


def test_status_refusal_text_as_is_and_reauth():
    j = FakeJira()
    j.fail["do_transition"] = JiraIntegrationError(
        "Необходимо добавить комментарий", code="jira_error", status=400)
    with pytest.raises(AssignError) as e:
        svc(j).change_status(USER, KEY, "no_candidates", comment="x",
                             fields={"customfield_16100": "23300"})
    assert e.value.code == "jira_error" and e.value.message == "Необходимо добавить комментарий"
    assert e.value.step == "transition"
    with pytest.raises(AssignError) as e:
        svc(FakeJira(), token=None).list_actions(USER, KEY)
    assert e.value.code == "needs_reauthorization"


# --- назначение ---

def test_assign_full_flow_order_and_payload():
    j = FakeJira(customfield_21105={"value": "от производства"})
    s = svc(j)
    r = s.assign(USER, KEY, AssignInput(EMP, extra_file_ids=(2, 3), rate_rub=Decimal("2500"),
                                         comment="скрининг пройден"))
    assert [n for n in j.names() if n != "get_issue"] == [
        "get_transitions", "set_fields", "attach_file", "attach_file", "attach_file",
        "do_transition", "set_rate"]                  # ставка - после назначения (CR-008)
    rate = next(c for c in j.calls if c[0] == "set_rate")
    assert rate[1:] == ("2500", "20999", "21001", "21006", "22412", USER)
    fields = next(c for c in j.calls if c[0] == "set_fields")[1]
    assert fields["customfield_10707"] == {"name": "emp.login"}      # профиль сотрудника
    assert fields["assignee"] == {"name": USER}                      # FR-009
    assert not {"customfield_20809", "customfield_20810", "customfield_20811",
                "customfield_20812"} & set(fields)      # ставка - не PUT /issue и не поле перехода
    assert [c[1] for c in j.calls if c[0] == "attach_file"] == ["f1.docx", "f2.docx", "f3.docx"]
    tr = next(c for c in j.calls if c[0] == "do_transition")
    assert tr[1] == "21" and tr[3] == "скрининг пройден"
    assert r.status == "Кандидат назначен" and r.refreshed and s.cache.rows[-1].status == r.status
    assert r.steps[-1] == "rate" and not r.rate_pending and r.warnings == ()
    assert j.tokens == {FAKE_SEALED}                                  # только токен пользователя


PROD = {"customfield_21105": {"value": "от производства"}}
CLIENT = {"customfield_21105": {"value": "от клиента"}}


def _no_rate_in_writes(j):
    assert not any(k.startswith("customfield_208") for c in j.calls if c[0] == "set_fields"
                   for k in c[1])
    tr = next(c for c in j.calls if c[0] == "do_transition")
    assert not any(k.startswith("customfield_208") for k in tr[2])


def test_assign_production_requires_rate_before_any_write():
    j = FakeJira(**PROD)
    with pytest.raises(AssignError) as e:
        svc(j).assign(USER, KEY, AssignInput(EMP))
    assert e.value.code == "rate_required"
    assert not {"set_fields", "attach_file", "do_transition", "set_rate"} & set(j.names())
    for bad in (Decimal("0"), Decimal("-1")):
        with pytest.raises(AssignError) as e:
            svc(FakeJira(**PROD)).assign(USER, KEY, AssignInput(EMP, rate_rub=bad))
        assert e.value.code == "bad_request"


def test_assign_client_proposer_never_sends_rate():
    j = FakeJira(**CLIENT)
    r = svc(j).assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("999")))   # и не требует её
    j2 = FakeJira(**CLIENT)
    svc(j2).assign(USER, KEY, AssignInput(EMP))
    for jj in (j, j2):
        assert "set_rate" not in jj.names()
        _no_rate_in_writes(jj)
    assert "rate" not in r.steps and not r.rate_pending


def test_assign_rate_failure_is_partial_success_and_retry_writes_only_rate():
    j = FakeJira(**PROD)
    j.fail["set_rate"] = JiraIntegrationError(
        "Jira ответила 200, но ставка не записана", code="jira_error")
    s = svc(j)
    r = s.assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("2700"), comment="к"))
    assert r.status == "Кандидат назначен" and r.rate_pending and "rate" not in r.steps
    assert "ставка не записана" in r.warnings[0] and "Кандидат назначен" in r.warnings[0]
    assert "transition" in r.steps                     # назначение не откатывается
    j.fail.clear()
    j.calls.clear()
    j.status = "Кандидат назначен"
    j.transitions = [T_SEARCH]                          # Jira уже не отдаёт переход 21
    r2 = s.assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("2700"), comment="к",
                                         completed=r.steps))
    assert not r2.rate_pending and r2.steps[-1] == "rate"
    assert not {"set_fields", "attach_file", "do_transition", "add_comment"} & set(j.names())
    assert [c[0] for c in j.calls if c[0] == "set_rate"] == ["set_rate"]


@pytest.mark.parametrize("code", ["no_permission", "jira_unavailable", "jira_error"])
def test_assign_rate_refusal_keeps_assignment_for_any_code(code):
    j = FakeJira(**PROD)
    j.fail["set_rate"] = JiraIntegrationError("отказ Jira", code=code, status=403)
    r = svc(j).assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("1")))
    assert r.rate_pending and "отказ Jira" in r.warnings[0] and "do_transition" in j.names()


@pytest.mark.parametrize("code", ["needs_reauthorization", "auth_failed"])
def test_assign_rate_expired_session_raises_with_completed_and_retry_writes_only_rate(code):
    """KON-116: истёкшая сессия на шаге ставки - не rate_pending, а ошибка с completed (UC-005)."""
    j = FakeJira(**PROD)
    j.fail["set_rate"] = JiraIntegrationError("Сессия истекла", code=code, status=401)
    s = svc(j)
    with pytest.raises(AssignError) as e:
        s.assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("2700"), comment="к"))
    err = e.value
    assert err.code == code and err.step == "rate"
    assert "transition" in err.completed and "rate" not in err.completed
    assert "do_transition" in j.names()                 # назначение не откатывается
    j.fail.clear()
    j.calls.clear()
    j.status = "Кандидат назначен"
    j.transitions = [T_SEARCH]
    r2 = s.assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("2700"), comment="к",
                                         completed=tuple(err.completed)))
    assert not r2.rate_pending and r2.steps[-1] == "rate"
    assert j.names().count("set_rate") == 1
    assert not {"set_fields", "attach_file", "do_transition", "add_comment"} & set(j.names())


def test_api_rate_expired_session_is_409_with_completed_not_rate_pending():
    j = FakeJira(**PROD)
    j.fail["set_rate"] = JiraIntegrationError("Сессия истекла", code=CODE_NEEDS_REAUTHORIZATION)
    client, headers, _ = _api(j)
    r = client.post(f"/needs/{KEY}/assign", headers=headers,
                    json={"employee_key": EMP, "rate_rub": 2700})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["state"] == "needs_reauthorization" and d["step"] == "rate"
    assert "transition" in d["completed"] and "rate" not in d["completed"]
    assert "rate_pending" not in d


def test_api_rate_server_error_and_timeout_stay_rate_pending():
    for exc in (JiraIntegrationError("Jira 500", code="jira_error", status=500),
                JiraIntegrationError("таймаут", code="jira_unavailable", status=503)):
        j = FakeJira(**PROD)
        j.fail["set_rate"] = exc
        client, headers, _ = _api(j)
        r = client.post(f"/needs/{KEY}/assign", headers=headers,
                        json={"employee_key": EMP, "rate_rub": 2700})
        assert r.status_code == 200 and r.json()["rate_pending"] is True
        assert "transition" in r.json()["steps"]


def test_assign_rate_sent_on_reassign_without_transition():
    j = FakeJira(status="Кандидат назначен", transitions=[T_SEARCH], **PROD)
    r = svc(j).assign(USER, KEY, AssignInput(EMP, rate_rub=Decimal("2700")))
    assert "do_transition" not in j.names() and r.steps[-1] == "rate"


def test_assign_validation_errors_before_writes():
    j = FakeJira()
    svc(j).assign(USER, KEY, AssignInput(EMP))
    _no_rate_in_writes(j)
    for bad in (AssignInput("EMP-9"), AssignInput(EMP, resume_file_id=4),
                AssignInput(EMP, extra_file_ids=(5,)), AssignInput(EMP, extra_file_ids=(6,))):
        j3 = FakeJira()
        with pytest.raises(AssignError):
            svc(j3).assign(USER, KEY, bad)
        assert not {"set_fields", "attach_file", "do_transition"} & set(j3.names())


def test_assign_unavailable_when_jira_offers_no_transition():
    j = FakeJira(status="Требуется кандидат", transitions=[T_SEARCH])
    with pytest.raises(AssignError) as e:
        svc(j).assign(USER, KEY, AssignInput(EMP))
    assert e.value.code == "assign_unavailable"
    assert "set_fields" not in j.names()


def test_reassign_from_assigned_status_skips_transition_and_comments():
    j = FakeJira(status="Кандидат назначен", transitions=[T_SEARCH])
    r = svc(j).assign(USER, KEY, AssignInput(EMP, comment="замена"))
    assert "do_transition" not in j.names()
    assert next(c for c in j.calls if c[0] == "add_comment")[1] == "замена"
    assert r.steps[-1] == "comment"


def test_assign_failure_keeps_completed_steps_and_retry_does_not_duplicate():
    j = FakeJira()
    j.fail["do_transition"] = JiraIntegrationError(
        "Переход запрещён правами", code="no_permission", status=403)
    s = svc(j)
    with pytest.raises(AssignError) as e:
        s.assign(USER, KEY, AssignInput(EMP, extra_file_ids=(2,), comment="к"))
    err = e.value
    assert err.code == "no_permission" and err.message == "Переход запрещён правами"
    assert err.step == "transition" and err.completed == ["fields", "attach:1", "attach:2"]
    j.fail.clear()
    j.calls.clear()
    s.assign(USER, KEY, AssignInput(EMP, extra_file_ids=(2,), comment="к",
                                    completed=tuple(err.completed)))
    assert "attach_file" not in j.names() and "set_fields" not in j.names()
    assert "do_transition" in j.names()


def test_assign_reread_failure_does_not_undo_operation():
    j = FakeJira()
    s = svc(j)
    orig = j.get_issue
    state = {"n": 0}

    def flaky(token, key, fields=None):
        state["n"] += 1
        if state["n"] > 1:
            raise JiraIntegrationError("down", code=CODE_JIRA_UNAVAILABLE)
        return orig(token, key, fields)

    j.get_issue = flaky
    r = s.assign(USER, KEY, AssignInput(EMP))
    assert r.refreshed is False and r.status is None and "transition" in r.steps


def test_assign_requires_stores_and_employee_profile():
    with pytest.raises(AssignError) as e:
        svc(FakeJira(), files=False).assign(USER, KEY, AssignInput(EMP))
    assert e.value.code == "not_enabled"
    s = AssignService(FakeJira(), lambda login: FAKE_SEALED, FakeEmployees(login=None),
                      FakeFiles(), None)
    with pytest.raises(AssignError) as e:
        s.assign(USER, KEY, AssignInput(EMP))
    assert e.value.code == "employee_no_profile"


# --- API ---

class _JiraSession:
    def __init__(self, state="ok"):
        self.state = state

    def status(self, login):
        return JiraStatusInfo(self.state)


def _api(jira=None, session="ok"):
    client, headers, app = make_client(None, None)
    app.include_router(assign_router)
    app.state.jira_session = _JiraSession(session)
    app.state.assign = svc(jira) if jira is not None else None
    return client, headers, app


def test_api_status_actions_and_status_change():
    j = FakeJira()
    client, headers, _ = _api(j)
    got = client.get(f"/needs/{KEY}/status-actions").json()
    assert [a["action"] for a in got["actions"]] == ["hire", "no_candidates"]
    assert client.post(f"/needs/{KEY}/status", json={"action": "hire"}).status_code == 403  # CSRF
    r = client.post(f"/needs/{KEY}/status", headers=headers, json={
        "action": "no_candidates", "comment": "нет", "fields": {"customfield_16100": "21300"}})
    assert r.status_code == 200 and r.json()["status"] == "Кандидатов нет"


def test_status_actions_only_on_click_one_jira_roundtrip():
    """KON-96: Jira читается только вызовом status-actions (по клику); один вызов - одно чтение
    потребности и один запрос переходов, записи нет."""
    j = FakeJira()
    client, _, _ = _api(j)
    assert j.calls == []  # конструирование сервиса и список потребностей Jira не трогают
    assert client.get(f"/needs/{KEY}/status-actions").status_code == 200
    assert [c[0] for c in j.calls] == ["get_issue", "get_transitions"]


def test_api_errors_text_as_is_with_completed_for_retry():
    j = FakeJira()
    j.fail["do_transition"] = JiraIntegrationError(
        "Пользователь не имеет права", code="no_permission", status=403)
    client, headers, _ = _api(j)
    r = client.post(f"/needs/{KEY}/assign", headers=headers,
                    json={"employee_key": EMP, "comment": "введено"})
    assert r.status_code == 403
    d = r.json()["detail"]
    assert d["message"] == "Пользователь не имеет права" and d["step"] == "transition"
    assert d["completed"] == ["fields", "attach:1"]
    j.fail["do_transition"] = JiraIntegrationError(
        "Сессия истекла", code=CODE_NEEDS_REAUTHORIZATION)
    r = client.post(f"/needs/{KEY}/assign", headers=headers,
                    json={"employee_key": EMP, "completed": d["completed"]})
    assert r.status_code == 409 and r.json()["detail"]["state"] == "needs_reauthorization"
    # 422: ставка перехода и некорректный ключ
    assert client.post(f"/needs/{KEY}/status", headers=headers,
                       json={"action": "hire", "rate": -1}).status_code == 422
    assert client.get("/needs/bad_key/status-actions").status_code == 422


def test_api_disabled_and_blocked_session():
    client, headers, _ = _api(None)
    r = client.get(f"/needs/{KEY}/status-actions")
    assert r.status_code == 503 and r.json()["detail"]["state"] == "not_enabled"
    client, headers, _ = _api(FakeJira(), session="blocked")
    r = client.post(f"/needs/{KEY}/assign", headers=headers, json={"employee_key": EMP})
    assert r.status_code == 409 and r.json()["detail"]["state"] == "blocked"


# --- JiraAdapter напрямую (моста нет): форма запросов Jira, лимиты, секреты ---

def _bridge(adapter_factory, vault, handler):
    adapter = adapter_factory(handler)
    token = vault.seal(FAKE_RAW_TOKEN)
    return adapter, token


def test_adapter_request_shapes_and_user_token(adapter_factory, vault):
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path.endswith("/transitions") and req.method == "GET":
            return response({"transitions": [{
                "id": "801", "name": "В найм", "to": {"id": "1", "name": "Найм"},
                "fields": {"customfield_20810": {"required": True, "name": "Включает НДС",
                                                 "fieldId": "customfield_20810",
                                                 "allowedValues": [
                                                     {"id": "20999", "value": "Да"}]}},
            }]})
        if req.url.path.endswith("/attachments"):
            return response([{"id": "1", "filename": "a.docx", "size": 3}], 200)
        return response(None, 204)

    port, token = _bridge(adapter_factory, vault, handler)
    t = port.get_transitions(token, KEY)
    assert t[0].to_status == "Найм" and t[0].fields[0].allowed_values[0].id == "20999"
    port.do_transition(token, KEY, "801", fields={"customfield_20810": {"id": "20999"}},
                       comment="к")
    body = json.loads(seen[-1].content)
    assert body == {"transition": {"id": "801"}, "fields": {"customfield_20810": {"id": "20999"}},
                    "update": {"comment": [{"add": {"body": "к"}}]}}
    port.set_fields(token, KEY, {"assignee": {"name": USER}})
    assert seen[-1].method == "PUT" and json.loads(seen[-1].content) == {
        "fields": {"assignee": {"name": USER}}}
    port.attach_file(token, KEY, "a.docx", b"abc", "application/msword")
    assert seen[-1].headers["X-Atlassian-Token"] == "no-check"
    # везде куки пользователя, в запросах записи нет логина/пароля
    assert all(FAKE_RAW_TOKEN in r.headers.get("cookie", "") for r in seen)


def test_adapter_maps_refusal_and_expired_session(adapter_factory, vault):
    def handler(req):
        if req.method == "PUT":
            return response({"errorMessages": ["Нет прав на редактирование"], "errors": {}}, 403)
        return response({"errorMessages": [], "errors": {}}, 401,
                        {"x-seraph-loginreason": "AUTHENTICATED_FAILED"})

    port, token = _bridge(adapter_factory, vault, handler)
    with pytest.raises(JiraIntegrationError) as e:
        port.set_fields(token, KEY, {"assignee": {"name": USER}})
    assert e.value.code == "no_permission" and "Нет прав на редактирование" in e.value.message
    with pytest.raises(JiraIntegrationError) as e:
        port.add_comment(token, KEY, "x")
    assert e.value.code == CODE_NEEDS_REAUTHORIZATION


def test_no_secrets_or_comment_text_in_logs(caplog):
    j = FakeJira()
    with caplog.at_level(logging.DEBUG):
        svc(j).assign(USER, KEY, AssignInput(EMP, comment="СЕКРЕТНЫЙ_ТЕКСТ_КОММЕНТАРИЯ"))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert FAKE_SEALED not in text and "СЕКРЕТНЫЙ_ТЕКСТ" not in text


# --- ставка на переходе «Найм» (CR-007) ---

T_HIRE_RATE = TransitionView("801", "В найм", "Найм", (
    TransitionField("customfield_20809", "Планируемая ставка", True, ()), VAT, CUR, UNIT))
HIRE_IDS = {"customfield_20810": "20999", "customfield_20811": "21001",
            "customfield_20812": "21006"}


def test_hire_never_sends_rate_fields_in_transition_801():
    # CR-008: при найме ставка не вводится; 20810-20812 из справочников/потребности
    j = FakeJira(transitions=[T_ASSIGN, T_HIRE_RATE], customfield_20809=2125.0, **PROD)
    svc(j).change_status(USER, KEY, "hire", fields={**HIRE_IDS, "customfield_20809": "3000"})
    sent = [c for c in j.calls if c[0] == "do_transition"]
    assert len(sent) == 1 and sent[0][1] == "801"
    assert sent[0][2] == {"customfield_20810": {"id": "20999"},
                          "customfield_20811": {"id": "21001"},
                          "customfield_20812": {"id": "21006"}}
    assert "set_fields" not in j.names() and "set_rate" not in j.names()


def test_hire_production_does_not_require_rate():
    j = FakeJira(transitions=[T_ASSIGN, T_HIRE_RATE], **PROD)
    svc(j).change_status(USER, KEY, "hire", fields=HIRE_IDS)
    assert "customfield_20809" not in next(c for c in j.calls if c[0] == "do_transition")[2]


def test_other_actions_never_send_rate_fields():
    j = FakeJira(customfield_20809=2125.0, transitions=[T_SEARCH, T_NONE])
    svc(j).change_status(USER, KEY, "no_candidates", comment="x",
                         fields={"customfield_16100": "23300"})
    sent = next(c for c in j.calls if c[0] == "do_transition")
    assert not any(k.startswith("customfield_208") for k in sent[2])


# --- обход правила «Нужно предоставить CV» (CR-006, KON-42 D1) ---

CV_REFUSAL = JiraIntegrationError(
    'attachment: Необходимо в поле "Вложение" добавить CV кандидата',
    code="jira_error", status=400)
CV_ON = {"customfield_11506": {"value": "Да"}}


def _cv_writes(j):
    return [c[1]["customfield_11506"]["value"] for c in j.calls
            if c[0] == "set_fields" and "customfield_11506" in c[1]]


def test_cv_bypass_success_sets_no_then_yes_in_order():
    j = FakeJira(**CV_ON)
    j.tr_errors = [CV_REFUSAL, None]
    r = svc(j).assign(USER, KEY, AssignInput(EMP))
    names = [n for n in j.names() if n not in ("get_issue", "get_transitions")]
    assert names == ["set_fields", "attach_file", "do_transition", "get_need_cv_rule",
                     "set_fields", "do_transition", "set_fields"]
    assert _cv_writes(j) == ["Нет", "Да"]
    assert r.status == "Кандидат назначен" and r.warnings == ()
    assert j.fields["customfield_11506"] == {"value": "Да"}


def test_cv_bypass_not_used_without_refusal_or_when_rule_off():
    j = FakeJira(**CV_ON)
    svc(j).assign(USER, KEY, AssignInput(EMP))           # первая попытка прошла
    assert _cv_writes(j) == [] and "get_need_cv_rule" not in j.names()
    j2 = FakeJira(customfield_11506={"value": "Нет"})    # правило выключено - обход не нужен
    j2.tr_errors = [CV_REFUSAL]
    with pytest.raises(AssignError) as e:
        svc(j2).assign(USER, KEY, AssignInput(EMP))
    assert _cv_writes(j2) == [] and "attachment" in e.value.message
    j3 = FakeJira(**CV_ON)                               # другой отказ - не обходим
    j3.tr_errors = [JiraIntegrationError("нет прав", code="no_permission", status=403)]
    with pytest.raises(AssignError):
        svc(j3).assign(USER, KEY, AssignInput(EMP))
    assert _cv_writes(j3) == []


def test_cv_bypass_restores_yes_when_transition_fails():
    j = FakeJira(**CV_ON)
    j.tr_errors = [CV_REFUSAL, JiraIntegrationError("Переход запрещён", code="jira_error",
                                                    status=400)]
    with pytest.raises(AssignError) as e:
        svc(j).assign(USER, KEY, AssignInput(EMP, comment="введено"))
    assert _cv_writes(j) == ["Нет", "Да"]                # «Да» возвращено несмотря на ошибку
    err = e.value
    assert err.code == "jira_error" and "Переход запрещён" in err.message
    assert "вручную" in err.message and "осталось" not in err.message
    assert err.step == "transition" and err.completed == ["fields", "attach:1"]


def test_cv_bypass_failed_restore_after_success_is_warning():
    j = FakeJira(**CV_ON)
    j.tr_errors = [CV_REFUSAL, None]
    j.set_errors["Да"] = JiraIntegrationError("редактирование запрещено", code="jira_error",
                                              status=400)
    r = svc(j).assign(USER, KEY, AssignInput(EMP))
    assert r.status == "Кандидат назначен" and "transition" in r.steps
    assert len(r.warnings) == 1 and "осталось «Нет»" in r.warnings[0]


def test_cv_bypass_failed_restore_after_failed_transition_is_explicit_error():
    j = FakeJira(**CV_ON)
    j.tr_errors = [CV_REFUSAL, JiraIntegrationError("всё равно 400", code="jira_error",
                                                    status=400)]
    j.set_errors["Да"] = JiraIntegrationError("нельзя", code="jira_error", status=400)
    with pytest.raises(AssignError) as e:
        svc(j).assign(USER, KEY, AssignInput(EMP))
    assert "осталось «Нет»" in e.value.message and "всё равно 400" in e.value.message
    assert e.value.step == "transition"


def test_cv_bypass_cannot_switch_rule_off_gives_manual_hint():
    j = FakeJira(**CV_ON)
    j.tr_errors = [CV_REFUSAL]
    j.set_errors["Нет"] = JiraIntegrationError("поле не редактируется", code="jira_error",
                                               status=400)
    with pytest.raises(AssignError) as e:
        svc(j).assign(USER, KEY, AssignInput(EMP))
    assert "вручную" in e.value.message and "поле не редактируется" in e.value.message
    assert _cv_writes(j) == ["Нет"]                      # возвращать нечего: значение не менялось
    assert j.names().count("do_transition") == 1


def test_cv_bypass_api_returns_warnings():
    j = FakeJira(**CV_ON)
    j.tr_errors = [CV_REFUSAL, None]
    j.set_errors["Да"] = JiraIntegrationError("нельзя", code="jira_error", status=400)
    client, headers, _ = _api(j)
    r = client.post(f"/needs/{KEY}/assign", headers=headers, json={"employee_key": EMP})
    assert r.status_code == 200 and "осталось «Нет»" in r.json()["warnings"][0]


def test_assign_api_writes_rate_and_reports_partial_success():
    j = FakeJira(**PROD)
    client, headers, _ = _api(j)
    r = client.post(f"/needs/{KEY}/assign", headers=headers,
                    json={"employee_key": EMP, "rate_rub": 3000})
    assert r.status_code == 200 and r.json()["warnings"] == [] and not r.json()["rate_pending"]
    assert "rate" in r.json()["steps"]
    j2 = FakeJira(**PROD)
    j2.fail["set_rate"] = JiraIntegrationError("500 Jira", code="jira_error", status=500)
    client2, headers2, _ = _api(j2)
    r = client2.post(f"/needs/{KEY}/assign", headers=headers2,
                     json={"employee_key": EMP, "rate_rub": 3000})
    body = r.json()
    assert r.status_code == 200 and body["rate_pending"] and "500 Jira" in body["warnings"][0]
    assert "rate" not in body["steps"] and "transition" in body["steps"]
    r = client.post(f"/needs/{KEY}/assign", headers=headers,
                    json={"employee_key": EMP, "rate_rub": 0})
    assert r.status_code == 422
    r = client.post(f"/needs/{KEY}/assign", headers=headers,
                    json={"employee_key": EMP})                 # «от производства» без ставки
    assert r.status_code == 422 and r.json()["detail"]["code"] == "rate_required"


# --- замена моста: поведение при 401/403/503 и D3 через настоящий JiraAdapter ---

@pytest.mark.parametrize("status,headers_,code", [
    (401, {"x-seraph-loginreason": "AUTHENTICATED_FAILED"}, CODE_NEEDS_REAUTHORIZATION),
    (403, {}, "no_permission"),
    (503, {}, CODE_JIRA_UNAVAILABLE),
])
def test_service_on_real_adapter_maps_401_403_503(adapter_factory, vault, status, headers_, code):
    adapter = adapter_factory(lambda req: response(
        {"errorMessages": ["отказ"], "errors": {}}, status, headers_))
    s = AssignService(adapter, lambda login: vault.seal(FAKE_RAW_TOKEN), FakeEmployees(),
                      FakeFiles(), None)
    with pytest.raises(AssignError) as e:
        s.list_actions(USER, KEY)
    assert e.value.code == code


def test_unknown_transition_500_is_not_retryable_unavailable(adapter_factory, vault):
    adapter = adapter_factory(lambda req: response(
        {"errorMessages": ["Внутренняя ошибка сервера"], "errors": {}}, 500))
    with pytest.raises(JiraIntegrationError) as e:
        adapter.do_transition(vault.seal(FAKE_RAW_TOKEN), KEY, "999999", fields={}, comment="к")
    assert e.value.code == "jira_error" and e.value.retryable is False
    # 503 по-прежнему «Jira недоступна»
    adapter503 = adapter_factory(lambda req: response({"errorMessages": ["x"]}, 503))
    with pytest.raises(JiraIntegrationError) as e:
        adapter503.do_transition(vault.seal(FAKE_RAW_TOKEN), KEY, "21", fields={}, comment="к")
    assert e.value.code == CODE_JIRA_UNAVAILABLE and e.value.retryable is True


# --- «Поддержка» на переходе 21 (KON-122) ---

SUPPORT = TransitionField("customfield_11602", "Поддержка", False,
                          av(("11501", "Группа 51"), ("11503", "  не  ТРЕБУЕТСЯ "),
                             ("11502", "Ментор")))
T_ASSIGN_SUP = TransitionView("21", "Кандидат назначен", "Кандидат назначен", (SUPPORT,))


def test_support_not_needed_sent_in_transition_fields_by_name():
    j = FakeJira(transitions=[T_ASSIGN_SUP], customfield_11602={"id": "11502"})
    s = svc(j)
    s.assign(USER, KEY, AssignInput(EMP))
    tr = next(c for c in j.calls if c[0] == "do_transition")
    assert tr[2]["customfield_11602"] == {"id": "11503"}   # даже при другом значении на потребности
    assert all("customfield_11602" not in c[1] for c in j.calls if c[0] == "set_fields")


def test_support_option_missing_is_error_before_any_write():
    other = TransitionField("customfield_11602", "Поддержка", False, av(("11501", "Группа 51")))
    t = TransitionView("21", "Кандидат назначен", "Кандидат назначен", (other,))
    j = FakeJira(transitions=[t])
    with pytest.raises(AssignError) as e:
        svc(j).assign(USER, KEY, AssignInput(EMP))
    assert e.value.code == "support_option_missing" and "Не требуется" in e.value.message
    assert not {"set_fields", "attach_file", "do_transition"} & set(j.names())


def test_support_field_absent_on_screen_transition_without_it():
    j = FakeJira()
    svc(j).assign(USER, KEY, AssignInput(EMP))
    tr = next(c for c in j.calls if c[0] == "do_transition")
    assert "customfield_11602" not in tr[2]


def test_support_retry_does_not_duplicate_attach_and_resends_field():
    j = FakeJira(transitions=[T_ASSIGN_SUP])
    j.tr_errors = [JiraIntegrationError("отказ", code="jira_error", status=400), None]
    s = svc(j)
    with pytest.raises(AssignError) as e:
        s.assign(USER, KEY, AssignInput(EMP))
    s.assign(USER, KEY, AssignInput(EMP, completed=tuple(e.value.completed)))
    assert j.names().count("attach_file") == 1
    trs = [c for c in j.calls if c[0] == "do_transition"]
    assert len(trs) == 2 and all(c[2]["customfield_11602"] == {"id": "11503"} for c in trs)
