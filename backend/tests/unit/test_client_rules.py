"""Тезисы правил клиента из комментариев эпика (KON-160): сервис, ленивый пересчёт, сбор.

Без сети и БД: фейки SyncRepo, LlmRepo и шлюза (_sync_fakes + локальные фейки ниже).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from dataclasses import replace as dc_replace

from app.client_rules.prompts import EPIC_RULES_SYSTEM, parse_result
from app.client_rules.service import (
    ST_EMPTY,
    ST_OK,
    ST_REGENERATING,
    ClientRulesError,
    ClientRulesService,
)
from app.sync.mapping import NeedRow

EPIC = "SA-4160"
NEED = "ANALYTICS-5938"
COMMENT_BODY = "Отдел кадров: CV только с фотографией"

THESIS_RESULT = json.dumps([
    {"text": "CV отправлять только с фотографией", "contradicts_rules": False,
     "sources": [{"id": "101"}]},
    {"text": "NDA подписан: условия не распространяются на подрядчиков",
     "contradicts_rules": True, "sources": [{"id": "101"}, {"id": "999"}]},
], ensure_ascii=False)


def need_row(epic_key: str | None = EPIC) -> NeedRow:
    return NeedRow(
        jira_key=NEED, title="Аналитик", description=None, requirements_text=None,
        status="Требуется кандидат", epic_key=epic_key, assignee=None, author=None,
        contract_type=None, rate_amount=None, rate_currency=None, rate_includes_vat=None,
        rate_unit=None, rate_proposer=None, jira_created_at=None, jira_updated_at=None,
        employee_login=None, employee_key=None, rate_dropped=False,
    )


@dataclass
class FakeTaskRow:
    id: str
    purpose: str
    status: str
    ref: str | None


@dataclass
class FakeTaskView:
    id: str
    purpose: str = "epic_rules"
    ref: str | None = None
    status: str = "queued"
    requires_confirmation: bool = False
    provider_name: str = "Qwen (внутренняя)"
    result: str | None = None


class FakeLlmRepo:
    def __init__(self) -> None:
        self.tasks: dict[str, FakeTaskRow] = {}
        self.cancelled: list[str] = []

    def create(self, ref: str, status: str = "queued") -> str:
        tid = str(uuid.uuid4())
        self.tasks[tid] = FakeTaskRow(tid, "epic_rules", status, ref)
        return tid

    def latest_task(self, purpose: str, ref: str) -> FakeTaskRow | None:
        rows = [t for t in self.tasks.values() if t.purpose == purpose and t.ref == ref]
        return rows[-1] if rows else None

    def cancel_task(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        return True


class FakeGateway:
    def __init__(self, llm_repo: "FakeLlmRepo | None" = None, *, external: bool = False) -> None:
        self.llm_repo = llm_repo
        self.external = external
        self.submitted: list[dict] = []
        self.views: dict[str, FakeTaskView] = {}

    def submit(self, owner_login, purpose, prompt, *, system=None, provider_id=None,
               ref=None, terms=None):
        tid = str(uuid.uuid4())
        self.submitted.append({
            "owner": owner_login, "purpose": purpose, "prompt": prompt, "system": system,
            "ref": ref,
        })
        view = FakeTaskView(id=tid, purpose=purpose, ref=ref,
                            status="awaiting_confirmation" if self.external else "queued",
                            requires_confirmation=self.external)
        self.views[tid] = view
        if self.llm_repo is not None:
            self.llm_repo.tasks[tid] = FakeTaskRow(tid, purpose, view.status, ref)
        return view

    def peek(self, task_id: str) -> FakeTaskView | None:
        return self.views.get(task_id)


def seed_basic(repo):
    """Потребность + эпик с правилами (комментарии не написаны)."""
    repo.needs[NEED] = need_row()
    repo.save_epic(EPIC, "Клиент Альфа", None, [{"id": "f1", "name": "Есть NDA", "value": "Да"}])


def seed_comments(repo, jira):
    """Комментарий в Jira-фейке и в кэше: хеш комментариев после первой записи кэша."""
    jira.add_comment(EPIC, "101", COMMENT_BODY)
    repo.upsert_epic_comments(EPIC, jira.get_comments("t", EPIC))


def make_service(*, repo=None, llm_repo=None, gateway=None):
    from ._sync_fakes import FakeSyncRepo

    repo = repo or FakeSyncRepo()
    llm_repo = llm_repo or FakeLlmRepo()
    gateway = gateway or FakeGateway(llm_repo)
    return ClientRulesService(repo, llm_repo, gateway), repo, llm_repo, gateway


# --- parse_result ---

def test_parse_result_normalizes():
    out = parse_result(
        '```json\n[{"text":"Тезис","contradicts_rules":true,"sources":[{"id":"101"}]}]\n```')
    assert out == [{"text": "Тезис", "contradicts_rules": True, "sources": [{"id": "101"}]}]


def test_parse_result_survives_prose_around_array():
    out = parse_result('Вот тезисы:\n[{"text":"A","sources":[]}]\nКонец ответа.')
    assert out and out[0]["text"] == "A" and out[0]["contradicts_rules"] is False


def test_parse_result_garbage_returns_empty():
    assert parse_result("мимо") == []
    assert parse_result("[{text без кавычек}]") == []


# --- GET theses ---

def test_get_ok_from_cache_with_sources():
    from ._sync_fakes import FakeJira

    service, repo, _, _ = make_service()
    seed_basic(repo)
    seed_comments(repo, FakeJira())
    repo.save_epic_theses(EPIC, repo.epic_comments_hash(EPIC),
                          [{"text": "CV с фото", "contradicts_rules": False,
                            "sources": [{"id": "101"}]}], "Qwen (внутренняя)")
    data = service.get(NEED)
    assert data["status"] == ST_OK and data["epic_key"] == EPIC
    assert data["theses"][0]["sources"][0]["author"] == "Клиентоса А.Б."
    assert data["provider"] == "Qwen (внутренняя)" and data["generated_at"]


def test_get_regenerating_submits_task_once():
    from ._sync_fakes import FakeJira

    service, repo, llm_repo, gateway = make_service()
    seed_basic(repo)
    seed_comments(repo, FakeJira())
    repo.save_epic_theses(EPIC, "старый-хеш", [], "Qwen")
    first = service.get(NEED)
    assert first["status"] == ST_REGENERATING and first["task_submitted"]
    prompt = gateway.submitted[0]["prompt"]
    assert "CV только с фотографией" in prompt and "[101]" in prompt
    assert gateway.submitted[0]["system"] == EPIC_RULES_SYSTEM
    assert gateway.submitted[0]["owner"] == "system"
    assert f"{EPIC}:" in first["epic_key"] or first["epic_key"] == EPIC
    # повторное открытие блока: задача уже висит, новую не ставим
    second = service.get(NEED)
    assert second["task_submitted"] is None
    assert len(gateway.submitted) == 1
    assert llm_repo.latest_task("epic_rules", gateway.submitted[0]["ref"]) is not None


def test_get_empty_when_no_comments():
    service, repo, _, _ = make_service()
    seed_basic(repo)
    data = service.get(NEED)
    assert data["status"] == ST_EMPTY and data["theses"] == []


def test_get_need_not_found():
    service, repo, _, _ = make_service()
    seed_basic(repo)
    try:
        service.get("ANALYTICS-404")
        raise AssertionError("должен был упасть")
    except ClientRulesError as e:
        assert e.status == 404


def test_get_need_without_client_409():
    service, repo, _, _ = make_service()
    repo.needs[NEED] = need_row(epic_key=None)
    try:
        service.get(NEED)
        raise AssertionError("должен был упасть")
    except ClientRulesError as e:
        assert e.status == 409


# --- collect (worker on_finished) ---

def test_collect_saves_and_drops_unknown_sources():
    from ._sync_fakes import FakeJira

    service, repo, llm_repo, gateway = make_service()
    seed_basic(repo)
    seed_comments(repo, FakeJira())
    h = repo.epic_comments_hash(EPIC)
    tid = gateway.submit("system", "epic_rules", "p", ref=f"{EPIC}:{h[:16]}").id
    view = gateway.views[tid]
    view.status = "done"
    view.result = THESIS_RESULT
    service.collect_task(tid)
    assert len(repo.saved_theses) == 1
    epic, saved_hash, theses, provider = repo.saved_theses[0]
    assert epic == EPIC and saved_hash == h[:16] and provider == "Qwen (внутренняя)"
    assert theses[0]["sources"] == [{"id": "101"}]  # источник 999, которого нет, отсеян
    assert theses[1]["contradicts_rules"] is True


def test_collect_ignores_failed_and_foreign_purpose():
    service, repo, _, gateway = make_service()
    seed_basic(repo)
    tid = gateway.submit("system", "epic_rules", "p", ref=f"{EPIC}:x").id
    view = gateway.views[tid]
    view.status = "failed"
    view.result = "[]"
    service.collect_task(tid)
    foreign = FakeGateway()
    tid2 = foreign.submit("system", "matching", "p", ref="anything").id
    foreign.views[tid2].status = "done"
    foreign.views[tid2].result = "[]"
    service.collect_task(tid2)
    assert repo.saved_theses == []


def test_collect_task_without_ref_is_ignored():
    service, repo, _, gateway = make_service()
    seed_basic(repo)
    tid = gateway.submit("system", "epic_rules", "p", ref=None).id
    view = gateway.views[tid]
    view.status = "done"
    view.result = "[{\"text\":\"A\"}]"
    service.collect_task(tid)
    assert repo.saved_theses == []


# --- regenerate (admin) ---

def test_regenerate_cancels_pending_and_submits_from_admin():
    from ._sync_fakes import FakeJira

    service, repo, llm_repo, gateway = make_service()
    seed_basic(repo)
    seed_comments(repo, FakeJira())
    _ = service.get(NEED)  # поставилась системная задача
    refs = {v.ref for v in gateway.views.values()}
    before = len(gateway.submitted)
    out = service.regenerate(NEED, "admin.login", provider_id=None)
    assert out["requires_confirmation"] is False and out["status"] == "queued"
    assert len(gateway.submitted) == before + 1
    assert gateway.submitted[-1]["owner"] == "admin.login"
    assert llm_repo.cancelled and llm_repo.tasks[llm_repo.cancelled[0]].ref in refs


def test_regenerate_external_requires_confirmation():
    from ._sync_fakes import FakeJira

    _, repo, llm_repo, _ = make_service()
    gateway = FakeGateway(llm_repo, external=True)
    service = ClientRulesService(repo, llm_repo, gateway)
    seed_basic(repo)
    seed_comments(repo, FakeJira())
    out = service.regenerate(NEED, "admin.login", provider_id=None)
    assert out["requires_confirmation"] is True and out["status"] == "awaiting_confirmation"


class _ExternalGateway(FakeGateway):
    """Внешний провайдер: задача в awaiting_confirmation (FR-023) — не используется;
    см. ``FakeGateway(external=True)``."""

    def submit(self, *args, **kwargs):
        view = super().submit(*args, **kwargs)
        view.status = "awaiting_confirmation"
        view.requires_confirmation = True
        return view


def test_regenerate_no_comments_409():
    service, repo, _, _ = make_service()
    seed_basic(repo)
    try:
        service.regenerate(NEED, "admin", None)
        raise AssertionError("должен был упасть")
    except ClientRulesError as e:
        assert e.status == 409


# --- догонка комментариев (sync service, KON-160 п. 1) ---

def test_needs_sync_catches_up_comments_of_fetched_epics():
    from app.sync import SyncService, SyncSettings

    from ._sync_fakes import FakeJira, FakeStore, FakeSyncRepo

    repo = FakeSyncRepo()
    jira = FakeJira()
    jira.issues = [  # потребность с эпиком
        {"key": NEED, "fields": {
            "summary": "Заявка", "status": {"name": "Требуется кандидат"},
            "created": "2026-10-01T10:00:00.000+0400",
            "updated": "2026-10-09T15:00:00.000+0400",
            "customfield_10101": EPIC,
        }},
    ]
    jira.add_comment(EPIC, "101", COMMENT_BODY)
    service = SyncService(repo, jira, FakeStore({"ivan": "t"}), SyncSettings())
    repo.set_poller("ivan")
    res = service.poll("needs")
    assert res.ok
    # комментарии эпика догонены при опросе потребностей (п. 1 схемы: догонка по updated)
    assert [c["id"] for c in repo.epic_comments(EPIC)] == ["101"]


def test_epic_view_catches_up_comments_on_reload():
    """Перечитали эпик ради «Правил» (TTL истёк) - комментарии догоняются точечно."""
    from datetime import UTC, datetime, timedelta

    from app.sync import SyncService, SyncSettings

    from ._sync_fakes import FakeJira, FakeStore, FakeSyncRepo

    repo = FakeSyncRepo()
    jira = FakeJira()
    jira.epic = {"name": "Клиент Альфа", "summary": "Эпик Альфа"}
    jira.epic_issue = {"key": EPIC, "fields": {"summary": "Эпик Альфа"}}
    jira.children = [{
        "key": NEED, "fields": {
            "summary": "Заявка", "status": {"name": "Требуется кандидат"},
            "created": "2026-10-01T10:00:00.000+0400",
            "updated": "2026-10-09T15:00:00.000+0400",
        },
    }]
    service = SyncService(repo, jira, FakeStore({"ivan": "t"}), SyncSettings())
    repo.set_poller("ivan")
    service.get_epic(EPIC, "ivan")
    assert repo.epic_comments(EPIC) == []  # комментариев ещё нет
    jira.add_comment(EPIC, "201", "Новый нюанс CV")
    # время: правила старше TTL - заставляем перечитать из Jira
    repo.epics[EPIC] = dc_replace(
        repo.epics[EPIC], rules_fetched_at=datetime.now(UTC) - timedelta(hours=2))
    service.get_epic(EPIC, "ivan")
    assert [c["id"] for c in repo.epic_comments(EPIC)] == ["201"]
