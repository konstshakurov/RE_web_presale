"""Подмены для тестов matching (KON-38): MatchRepo в памяти с теми же правилами, что в SQL,
потребности и шлюз LLM (настоящий LlmGateway поверх репозитория в памяти из _llm_fakes).

Сотрудников, CV и потребностей с реальными данными нет: имена и тексты придуманы для теста.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime

from app.matching.repo import BatchRow as _BatchRow
from app.matching.repo import (
    CandRow,
    EmployeeCard,
    NeedRow,
    RecRow,
    ResumeRow,
    RunRow,
    ScreenRow,
    SearchBatchRow,
    SearchResRow,
    SearchRunRow,
    SkillState,
)
from app.matching.service import MatchingService
from app.matching.settings import StaticSettings
from app.sync.mapping import NOT_ASSIGNABLE_STATUSES

from ._llm_fakes import Env, FakeClient

T_OLD = datetime(2026, 9, 1, tzinfo=UTC)
T_NEED = datetime(2026, 10, 10, tzinfo=UTC)
T_NEED2 = datetime(2026, 10, 11, tzinfo=UTC)


class FakeNeeds:
    def __init__(self) -> None:
        self.items: dict[str, NeedRow] = {}

    def add(self, key="ANALYTICS-1", title="Нужен системный аналитик", description="REST, SQL",
            requirements="Опыт 3 года, BPMN", updated=T_NEED, tags=()):
        self.items[key] = NeedRow(key, title, description, requirements, "Новая", updated,
                                  tuple(tags))

    def touch(self, key, when):
        n = self.items[key]
        self.items[key] = NeedRow(n.jira_key, n.title, n.description, n.requirements_text,
                                  n.status, when, n.tags)

    def get_need(self, key):
        return self.items.get(key)


class FakeMatchRepo:
    def __init__(self) -> None:
        self.employees: dict[str, dict] = {}
        self.files: dict[int, dict] = {}
        self.skills: dict[int, list[str]] = {}
        self.states: dict[int, dict] = {}
        self.recs: dict[tuple[str, int, str], dict] = {}
        self.runs: dict[str, RunRow] = {}
        self.batches: list[dict] = []
        self.sruns: dict[tuple[str, str], SearchRunRow] = {}
        self.sbatches: list[dict] = []
        self.sresults: dict[tuple[str, int, str], dict] = {}
        self.mscreens: dict[tuple[str, int, str], dict] = {}
        self.sscreens: dict[tuple[str, int, str], dict] = {}
        self._fid = 0
        self._bid = 0
        self.tick = 0

    # --- данные сотрудников ---
    def add_employee(self, key, name, *, status="Свободен", assignable=True, text="Текст CV",
                     note=None, note_updated_at=None, extra=None) -> int:
        self.employees[key] = dict(
            name=name, status=status, assignable=assignable, note=note,
            note_author="РО" if note else None, note_updated_at=note_updated_at, extra=extra)
        return self.set_main_cv(key, text)

    def set_main_cv(self, key, text) -> int:
        for f in self.files.values():
            if f["emp"] == key and not f["archived"]:
                f["archived"] = True
        self._fid += 1
        self.files[self._fid] = dict(emp=key, text=text, archived=False, name="cv.docx",
                                     status="ok")
        return self._fid

    def _current(self, key):
        for fid, f in self.files.items():
            if f["emp"] == key and not f["archived"]:
                return fid, f
        return None

    def list_candidates(self, *, only_assignable=True):
        out = []
        for key, e in sorted(self.employees.items(), key=lambda kv: (kv[1]["name"], kv[0])):
            cur = self._current(key)
            if cur is None or cur[1]["status"] != "ok":
                continue
            if only_assignable and e["status"] in NOT_ASSIGNABLE_STATUSES:
                continue
            fid, f = cur
            out.append(CandRow(key, e["name"], e["status"], e["assignable"], e["note"],
                               e["note_author"], e["note_updated_at"], e["extra"], fid,
                               f["name"], tuple(sorted(self.skills.get(fid, [])))))
        return out

    def get_main_cv(self, key):
        cur = self._current(key)
        return (cur[0], cur[1]["text"]) if cur and cur[1]["status"] == "ok" else None

    def get_cv_texts(self, ids):
        return {i: self.files[i]["text"] for i in ids if i in self.files}

    def get_employee(self, key):
        e = self.employees.get(key)
        if e is None:
            return None
        return EmployeeCard(key, e["name"], e["status"], e["assignable"], e["extra"], None,
                            e["note"], e["note_author"], e["note_updated_at"], T_OLD)

    def get_skills(self, fid):
        return tuple(sorted(self.skills.get(fid, [])))

    def get_skills_many(self, ids):
        return {i: tuple(sorted(self.skills[i])) for i in ids if self.skills.get(i)}

    def list_resume(self, *, query, status, tags, match_all, limit, offset):
        rows = []
        for key, e in sorted(self.employees.items(), key=lambda kv: kv[1]["name"]):
            cur = self._current(key)
            fid = cur[0] if cur else None
            sk = set(self.skills.get(fid, [])) if fid else set()
            if status and e["status"] != status:
                continue
            if tags and not (set(tags) <= sk if match_all else set(tags) & sk):
                continue
            if query and query.lower() not in (e["name"] + key + (cur[1]["text"] if cur else "")
                                               ).lower():
                continue
            st = self.states.get(fid)
            rows.append(ResumeRow(key, e["name"], e["status"], e["assignable"], e["extra"], None,
                                  e["note"], e["note_author"], e["note_updated_at"], fid,
                                  cur[1]["name"] if cur else None,
                                  cur[1]["status"] if cur else None, tuple(sorted(sk)),
                                  st["status"] if st else None))
        return rows[offset:offset + limit], len(rows)

    def list_technologies(self, limit=500):
        cnt: dict[str, int] = {}
        for fid, tags in self.skills.items():
            if not self.files[fid]["archived"]:
                for t in tags:
                    cnt[t] = cnt.get(t, 0) + 1
        return sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]

    # --- навыки ---
    def _state(self, fid) -> SkillState | None:
        s = self.states.get(fid)
        if s is None:
            return None
        return SkillState(fid, s["status"], s["task_id"], s["model"], s["by"], s["code"],
                          s["msg"], s["at"], T_OLD)

    def get_skill_state(self, fid):
        return self._state(fid)

    def state_by_task(self, task_id):
        return next((self._state(f) for f, s in self.states.items() if s["task_id"] == task_id),
                    None)

    def pending_skill_states(self, limit=100):
        return [self._state(f) for f, s in self.states.items()
                if s["status"] == "pending" and s["task_id"]]

    def claim_skills(self, fid, by, model):
        s = self.states.get(fid)
        if s is not None and s["status"] != "failed":
            return False
        self.states[fid] = dict(status="pending", task_id=None, model=model, by=by, code=None,
                                msg=None, at=None)
        return True

    def set_skills_task(self, fid, task_id):
        self.states[fid]["task_id"] = task_id

    def finish_skills_ok(self, fid, tags):
        s = self.states[fid]
        if s["status"] != "pending":
            return False
        s.update(status="ok", at=T_OLD)
        self.skills[fid] = list(tags)
        return True

    def finish_skills_failed(self, fid, code, msg):
        s = self.states[fid]
        if s["status"] != "pending":
            return False
        s.update(status="failed", code=code, msg=msg)
        return True

    # --- рекомендации ---
    def upsert_recommendations(self, need_key, recs, *, model, need_updated_at):
        for r in recs:
            f = self.files.get(r.cv_file_id)
            if f is None or f["emp"] != r.employee_key:
                continue
            self.tick += 1
            self.recs[(need_key, r.cv_file_id, model)] = dict(
                emp=r.employee_key, pct=r.match_percent, why=r.rationale,
                need_at=need_updated_at, snap=r.note_snapshot_at, order=self.tick,
                gaps=None if r.gaps is None else list(r.gaps))
        return len(recs)

    def list_recommendations(self, need_key):
        rows = [(k, v) for k, v in self.recs.items() if k[0] == need_key]
        rows.sort(key=lambda kv: -kv[1]["order"])
        return [RecRow(v["emp"], k[1], k[2], v["pct"], v["why"], v["need_at"], v["snap"],
                       datetime(2026, 10, 12, tzinfo=UTC), v.get("gaps")) for k, v in rows]

    # --- прогоны ---
    @contextmanager
    def run_tx(self, need_key):
        repo = self

        class Tx:
            def run(self_):
                return repo.runs.get(need_key)

            def batches(self_):
                return repo.list_batches(need_key)

            def replace(self_, run, batches):
                repo.runs[need_key] = RunRow(
                    need_key, run.requested_by, run.provider_id, run.model,
                    run.need_jira_updated_at, datetime(2026, 10, 12, 9, tzinfo=UTC),
                    run.chain_state)
                repo.batches = [b for b in repo.batches if b["need"] != need_key]
                for b in batches:
                    repo._bid += 1
                    repo.batches.append(dict(
                        id=repo._bid, need=need_key, task_id=b.task_id, keys=b.employee_keys,
                        fids=b.file_ids, status="pending", code=None, msg=None,
                        stage=b.stage, run_id=b.run_id))

            def finish_chain(self_):
                r = repo.runs.get(need_key)
                if r is not None:
                    repo.runs[need_key] = RunRow(
                        r.need_key, r.requested_by, r.provider_id, r.model,
                        r.need_jira_updated_at, r.created_at, "done")
        yield Tx()

    def get_run(self, need_key):
        return self.runs.get(need_key)

    def _b(self, b) -> _BatchRow:
        return _BatchRow(b["id"], b["need"], b["task_id"], tuple(b["keys"]), tuple(b["fids"]),
                         b["status"], b["code"], b["msg"], b["stage"], b["run_id"])

    def batches_by_run(self, run_id):
        return [self._b(b) for b in self.batches if b["run_id"] == run_id]

    def search_batches_by_run(self, run_id):
        return [self._sb(b) for b in self.sbatches if b["run_id"] == run_id]

    def list_match_screens(self, need_key):
        rows = [(k, v) for k, v in self.mscreens.items() if k[0] == need_key]
        rows.sort(key=lambda kv: -kv[1]["order"])
        return [ScreenRow(v["emp"], k[1], k[2], v["pct"], v["hash"], v["need_at"],
                          datetime(2026, 10, 12, tzinfo=UTC)) for k, v in rows]

    def upsert_match_screens(self, need_key, rows, *, model, need_updated_at):
        for r in rows:
            f = self.files.get(r.cv_file_id)
            if f is None or f["emp"] != r.employee_key:
                continue
            self.tick += 1
            self.mscreens[(need_key, r.cv_file_id, model)] = dict(
                emp=r.employee_key, pct=r.match_percent, hash=r.skills_hash,
                need_at=need_updated_at, order=self.tick)
        return len(rows)

    def list_search_screens(self, query_hash):
        rows = [(k, v) for k, v in self.sscreens.items() if k[0] == query_hash]
        rows.sort(key=lambda kv: -kv[1]["order"])
        return [ScreenRow(v["emp"], k[1], k[2], v["pct"], v["hash"], None,
                          datetime(2026, 10, 12, tzinfo=UTC)) for k, v in rows]

    def upsert_search_screens(self, query_hash, rows, *, model):
        for r in rows:
            f = self.files.get(r.cv_file_id)
            if f is None or f["emp"] != r.employee_key:
                continue
            self.tick += 1
            self.sscreens[(query_hash, r.cv_file_id, model)] = dict(
                emp=r.employee_key, pct=r.match_percent, hash=r.skills_hash, order=self.tick)
        return len(rows)

    def list_batches(self, need_key):
        return [self._b(b) for b in self.batches if b["need"] == need_key]

    def batch_by_task(self, task_id):
        return next((self._b(b) for b in self.batches if b["task_id"] == task_id), None)

    def pending_batches(self, limit=200):
        return [self._b(b) for b in self.batches if b["status"] == "pending"]

    def finish_batch(self, bid, status, code=None, message=None):
        b = next(b for b in self.batches if b["id"] == bid)
        if b["status"] != "pending":
            return False
        b.update(status=status, code=code, msg=message)
        return True


    # --- поиск по смыслу ---
    @contextmanager
    def search_tx(self, login, query_hash):
        repo = self

        class Tx:
            def run(self_):
                return repo.sruns.get((login, query_hash))

            def batches(self_):
                return repo.list_search_batches(login, query_hash)

            def replace(self_, run, batches):
                repo.sruns[(login, query_hash)] = SearchRunRow(
                    login, query_hash, run.query_text, run.provider_id, run.model,
                    datetime(2026, 10, 12, 9, tzinfo=UTC), run.chain_state, run.scope)
                repo.sbatches = [b for b in repo.sbatches
                                 if (b["login"], b["hash"]) != (login, query_hash)]
                for b in batches:
                    repo._bid += 1
                    repo.sbatches.append(dict(
                        id=repo._bid, login=login, hash=query_hash, task_id=b.task_id,
                        keys=b.employee_keys, fids=b.file_ids, status="pending", code=None,
                        msg=None, stage=b.stage, run_id=b.run_id))

            def finish_chain(self_):
                r = repo.sruns.get((login, query_hash))
                if r is not None:
                    repo.sruns[(login, query_hash)] = SearchRunRow(
                        r.requested_by, r.query_hash, r.query_text, r.provider_id, r.model,
                        r.created_at, "done", r.scope)
        yield Tx()

    def get_search_run(self, login, query_hash):
        return self.sruns.get((login, query_hash))

    def _sb(self, b) -> SearchBatchRow:
        return SearchBatchRow(b["id"], b["login"], b["hash"], b["task_id"], tuple(b["keys"]),
                              tuple(b["fids"]), b["status"], b["code"], b["msg"], b["stage"],
                              b["run_id"])

    def list_search_batches(self, login, query_hash):
        return [self._sb(b) for b in self.sbatches
                if (b["login"], b["hash"]) == (login, query_hash)]

    def search_batch_by_task(self, task_id):
        return next((self._sb(b) for b in self.sbatches if b["task_id"] == task_id), None)

    def finish_search_batch(self, bid, status, code=None, message=None):
        b = next(b for b in self.sbatches if b["id"] == bid)
        if b["status"] != "pending":
            return False
        b.update(status=status, code=code, msg=message)
        return True

    def upsert_search_results(self, query_hash, recs, *, model):
        for r in recs:
            f = self.files.get(r.cv_file_id)
            if f is None or f["emp"] != r.employee_key:
                continue
            self.tick += 1
            self.sresults[(query_hash, r.cv_file_id, model)] = dict(
                emp=r.employee_key, pct=r.match_percent, why=r.rationale, snap=r.note_snapshot_at,
                order=self.tick, gaps=None if r.gaps is None else list(r.gaps))
        return len(recs)

    def list_search_results(self, query_hash):
        rows = [(k, v) for k, v in self.sresults.items() if k[0] == query_hash]
        rows.sort(key=lambda kv: -kv[1]["order"])
        return [SearchResRow(v["emp"], k[1], k[2], v["pct"], v["why"], v["snap"],
                             datetime(2026, 10, 12, tzinfo=UTC), v.get("gaps")) for k, v in rows]

    def purge_search(self, retention_days):
        self.purged = retention_days


def reply_for(prompt: str, percent=lambda n: 90 - n) -> str:
    """Ответ «модели» на запрос сопоставления: оценка каждому K<n> из запроса."""
    labels = [ln[4:].strip() for ln in prompt.splitlines() if ln.startswith("### ")]
    return json.dumps([
        {"id": lb, "percent": percent(i), "rationale": f"обоснование {lb}",
         "gaps": [] if percent(i) >= 100 else [f"нет опыта Kafka ({lb})", "нет домена банки"]}
        for i, lb in enumerate(labels)], ensure_ascii=False)


def skills_reply(_prompt: str) -> str:
    return '["SQL", "BPMN", " rest api ", "sql"]'


class World:
    """Шлюз + хранилище + сервис; ``client`` отвечает по назначению запроса."""

    def __init__(self, *, max_evaluated=30, screen_batch=50, external_default=False) -> None:
        self.llm_client = FakeClient(self._reply)
        self.env = Env(client=self.llm_client)
        if external_default:
            self.env.providers.update(self.env.external.id, is_default=True)
        self.repo = FakeMatchRepo()
        self.needs = FakeNeeds()
        self.settings = StaticSettings(max_evaluated, screen_batch)
        self.svc = MatchingService(self.repo, self.needs, self.env.gateway,
                                   settings=self.settings)
        self.match_reply = reply_for
        self.skills_reply = skills_reply

    def _reply(self, prompt: str) -> str:
        if prompt.startswith(("ПОТРЕБНОСТЬ", "ЗАПРОС РУКОВОДИТЕЛЯ")):
            return self.match_reply(prompt)
        return self.skills_reply(prompt)

    def run_all(self) -> int:
        """Выполнить очередь шлюза как worker и собрать результаты как обработчик worker."""
        n = 0
        while (task := self.env.gateway.run_next()) is not None:
            self.svc.collect_task(task.id)
            n += 1
        return n
