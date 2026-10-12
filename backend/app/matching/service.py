"""Модуль matching (KON-38): навыки из Основного CV (FR-027), сопоставление потребности с
кандидатами (FR-005, FR-036), профиль (FR-003), фильтр по технологии (FR-032).

Все обращения к LLM идут только через шлюз ``llm_gateway`` (ADR-007): модуль ставит задачи
(``submit``) и забирает результат (``peek``), сам в сеть не ходит. Результат собирается двумя
путями, оба идемпотентны: worker после выполнения задачи (``collect_task``) и ленивый сбор при
запросе состояния. Тексты CV, заметки и ответы LLM в логи не попадают.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from .prompts import (
    MATCH_SYSTEM,
    SKILLS_SYSTEM,
    Candidate,
    NeedText,
    ResponseError,
    build_match_prompt,
    build_skills_prompt,
    candidate_block,
    parse_match,
    parse_skills,
)
from .repo import (
    DONE,
    FAILED,
    PENDING,
    BatchRow,
    CandRow,
    MatchRepo,
    NeedRow,
    NewBatch,
    NewRec,
    RecRow,
    RunRow,
    SkillState,
)
from .selection import MAX_EVALUATED, need_text, preselect, split_batches

log = logging.getLogger(__name__)

PURPOSE_MATCH = "match"
PURPOSE_SKILLS = "skills"
BACKFILL_LIMIT = 200

#: Итоговый статус прогона
NONE, RUNNING, AWAITING, DONE_RUN, PARTIAL, FAILED_RUN = (
    "none", "running", "awaiting_confirmation", "done", "partial", "failed")


class MatchingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NeedSource(Protocol):
    def get_need(self, key: str) -> NeedRow | None: ...


class Gateway(Protocol):
    """Часть интерфейса ``LlmGateway``, которой пользуется модуль."""

    def resolve_provider(self, provider_id: int | None = None): ...
    def submit(self, owner_login: str, purpose: str, prompt: str, *, system: str | None = None,
               provider_id: int | None = None, ref: str | None = None, terms=None): ...
    def peek(self, task_id: str): ...
    def cancel(self, owner_login: str, task_id: str): ...


@dataclass(frozen=True)
class TaskInfo:
    task_id: str
    status: str  # статус задачи шлюза либо done/failed по итогу сбора
    requires_confirmation: bool
    batch_status: str
    candidates: int


@dataclass(frozen=True)
class MatchItem:
    employee_key: str
    full_name: str
    status: str
    percent: int
    rationale: str
    note: str | None
    note_author: str | None
    note_updated_at: datetime | None
    cv_file_id: int
    calculated_at: datetime
    stale: bool
    skills: tuple[str, ...]


@dataclass(frozen=True)
class MatchState:
    need_key: str
    status: str
    requested_by: str | None
    model: str | None
    started_at: datetime | None
    calculated_at: datetime | None
    stale: bool
    candidates_total: int      # допустимые к назначению с основным CV
    evaluated: int             # кандидаты с оценкой в кэше
    total: int                 # в выдаче (=evaluated)
    items: list[MatchItem]
    tasks: list[TaskInfo]
    batches_total: int = 0
    batches_done: int = 0
    batches_failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillsResult:
    employee_key: str
    outcome: str  # scheduled | exists | no_cv | no_provider | error
    task_id: str | None = None
    message: str | None = None


def _label_map(keys: tuple[str, ...]) -> dict[str, str]:
    return {f"K{i + 1}": k for i, k in enumerate(keys)}


def _same(a: datetime | None, b: datetime | None) -> bool:
    return a == b


class MatchingService:
    def __init__(
        self, repo: MatchRepo, needs: NeedSource, gateway: Gateway,
        *, max_evaluated: int = MAX_EVALUATED,
    ) -> None:
        self._repo = repo
        self._needs = needs
        self._gw = gateway
        self._max = max_evaluated

    # ------------------------------------------------------------------ навыки (FR-027)

    def schedule_skills(self, employee_key: str, login: str) -> SkillsResult:
        """Поставить выделение навыков Основного CV. Только если файла ещё не извлекали (или
        прошлая попытка упала): без замены файла навыки не пересчитываются."""
        main = self._repo.get_main_cv(employee_key)
        if main is None:
            return SkillsResult(employee_key, "no_cv")
        file_id, text = main
        if not text.strip():
            return SkillsResult(employee_key, "no_cv", message="В CV нет текста")
        try:
            provider = self._gw.resolve_provider(None)
        except Exception as exc:  # noqa: BLE001 - GatewayError: нет провайдера
            return SkillsResult(employee_key, "no_provider", message=str(exc))
        if not self._repo.claim_skills(file_id, login, provider.model):
            return SkillsResult(employee_key, "exists")
        try:
            view = self._gw.submit(
                login, PURPOSE_SKILLS, build_skills_prompt(text), system=SKILLS_SYSTEM,
                provider_id=provider.id, ref=employee_key)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            self._repo.finish_skills_failed(file_id, str(code), "Задача LLM не поставлена")
            log.warning("навыки: задача не поставлена", extra={
                "employee_key": employee_key, "error": str(code)})
            return SkillsResult(employee_key, "error", message=str(getattr(exc, "message", code)))
        self._repo.set_skills_task(file_id, view.id)
        log.info("навыки: задача поставлена", extra={
            "employee_key": employee_key, "task_id": view.id})
        return SkillsResult(employee_key, "scheduled", task_id=view.id)

    def backfill_skills(self, login: str, limit: int = BACKFILL_LIMIT) -> list[SkillsResult]:
        """Поставить навыки всем, у кого основное CV есть, а навыков ещё нет (загруженные до
        появления модуля). Уже извлечённые и идущие пропускаются."""
        out: list[SkillsResult] = []
        for c in self._repo.list_candidates(only_assignable=False):
            if len(out) >= limit:
                break
            st = self._repo.get_skill_state(c.file_id)
            if st is not None and st.status != FAILED:
                continue
            out.append(self.schedule_skills(c.employee_key, login))
        return out

    def _collect_skills(self, st: SkillState) -> None:
        if st.status != PENDING or st.task_id is None:
            return
        view = self._gw.peek(st.task_id)
        if view is None:
            self._repo.finish_skills_failed(st.file_id, "task_lost", "Задача LLM не найдена")
            return
        if view.status == "done":
            try:
                tags = parse_skills(view.result or "")
            except ResponseError as exc:
                self._repo.finish_skills_failed(
                    st.file_id, "bad_response", f"Ответ LLM не разобран: {exc}")
                return
            self._repo.finish_skills_ok(st.file_id, tags)
            log.info("навыки: извлечены", extra={"task_id": st.task_id, "count": len(tags)})
        elif view.status in ("failed", "timeout", "cancelled"):
            self._repo.finish_skills_failed(
                st.file_id, view.error_code or view.status,
                view.error_message or "Задача LLM не выполнена")

    def collect_pending_skills(self) -> int:
        n = 0
        for st in self._repo.pending_skill_states():
            before = st.status
            self._collect_skills(st)
            after = self._repo.get_skill_state(st.file_id)
            if after is not None and after.status != before:
                n += 1
        return n

    # ------------------------------------------------------------------ сопоставление

    def start_match(
        self, need_key: str, login: str, *, provider_id: int | None = None, force: bool = False,
    ) -> MatchState:
        need = self._needs.get_need(need_key)
        if need is None:
            raise MatchingError("need_not_found", "Потребность не найдена в кэше Jira")
        provider = self._gw.resolve_provider(provider_id)  # GatewayError - как есть
        self._refresh(need_key)
        cands = self._repo.list_candidates(only_assignable=True)
        recs = self._repo.list_recommendations(need_key)

        selected = preselect(
            [(c.employee_key, c.full_name, c.skills) for c in cands],
            need_text(need.title, need.description, need.requirements_text, need.tags),
            self._max)
        by_key = {c.employee_key: c for c in cands}
        order = [by_key[s.key] for s in selected]
        todo = order if force else [
            c for c in order if not self._is_fresh(c, need, provider.model, recs)]

        if todo:
            self._submit_run(need, login, provider.id, provider.model, todo)
        else:
            log.info("сопоставление: всё в кэше", extra={"need_key": need_key})
        return self.get_state(need_key, limit=10, offset=0)

    @staticmethod
    def _is_fresh(c: CandRow, need: NeedRow, model: str, recs: list[RecRow]) -> bool:
        return any(
            r.employee_key == c.employee_key and r.cv_file_id == c.file_id and r.model == model
            and r.need_jira_updated_at == need.jira_updated_at
            and _same(r.note_snapshot_at, c.note_updated_at)
            for r in recs)

    def _submit_run(
        self, need: NeedRow, login: str, provider_id: int, model: str, todo: list[CandRow],
    ) -> None:
        texts = self._repo.get_cv_texts([c.file_id for c in todo])
        parts = [(c, texts.get(c.file_id, "")) for c in todo]
        sizes = [len(candidate_block(self._cand("K0", c, t))) for c, t in parts]
        groups = split_batches(sizes)
        nt = NeedText(need.title, need.description, need.requirements_text)
        submitted: list[str] = []
        with self._repo.run_tx(need.jira_key) as tx:
            if any(b.status == PENDING for b in tx.batches()):
                return  # прогон уже идёт: второй не запускаем
            new_batches: list[NewBatch] = []
            try:
                for idx in groups:
                    chunk = [parts[i] for i in idx]
                    cands = [self._cand(f"K{n + 1}", c, t) for n, (c, t) in enumerate(chunk)]
                    view = self._gw.submit(
                        login, PURPOSE_MATCH, build_match_prompt(nt, cands), system=MATCH_SYSTEM,
                        provider_id=provider_id, ref=need.jira_key)
                    submitted.append(view.id)
                    new_batches.append(NewBatch(
                        view.id, tuple(c.employee_key for c, _ in chunk),
                        tuple(c.file_id for c, _ in chunk)))
                tx.replace(
                    RunRow(need.jira_key, login, provider_id, model, need.jira_updated_at,
                           need.jira_updated_at),
                    new_batches)
            except Exception:
                for tid in submitted:  # не оставляем задач без прогона
                    try:
                        self._gw.cancel(login, tid)
                    except Exception:  # noqa: BLE001
                        pass
                raise
        log.info("сопоставление: прогон создан", extra={
            "need_key": need.jira_key, "batches": len(groups), "candidates": len(todo)})

    @staticmethod
    def _cand(label: str, c: CandRow, text: str) -> Candidate:
        return Candidate(label, c.employee_key, c.skills, text, c.note, c.extra_experience)

    def collect_task(self, task_id: str) -> bool:
        """Подобрать результат завершённой задачи шлюза (worker и ленивый сбор). True, если
        задача принадлежит модулю и обработана."""
        batch = self._repo.batch_by_task(task_id)
        if batch is not None:
            return self._collect_batch(batch)
        st = self._repo.state_by_task(task_id)
        if st is not None:
            self._collect_skills(st)
            return True
        return False

    def _collect_batch(self, b: BatchRow) -> bool:
        if b.status != PENDING:
            return False
        view = self._gw.peek(b.task_id)
        if view is None:
            self._repo.finish_batch(b.id, FAILED, "task_lost", "Задача LLM не найдена")
            return True
        if view.status in ("failed", "timeout", "cancelled"):
            self._repo.finish_batch(
                b.id, FAILED, view.error_code or view.status,
                view.error_message or "Задача LLM не выполнена")
            return True
        if view.status != "done":
            return False
        run = self._repo.get_run(b.need_key)
        if run is None:
            return False
        try:
            results = parse_match(view.result or "", _label_map(b.employee_keys))
        except ResponseError as exc:
            self._repo.finish_batch(
                b.id, FAILED, "bad_response", f"Ответ LLM не разобран: {exc}")
            return True
        recs: list[NewRec] = []
        for key, file_id in zip(b.employee_keys, b.file_ids, strict=True):
            res = results.get(key)
            if res is None:
                continue
            emp = self._repo.get_employee(key)
            snap = emp.note_updated_at if emp else None
            if snap is not None and snap > view.created_at:
                snap = view.created_at  # заметка правилась после постановки: кэш устаревший
            recs.append(NewRec(key, file_id, res.percent, res.rationale, snap))
        self._repo.upsert_recommendations(
            b.need_key, recs, model=run.model, need_updated_at=run.need_jira_updated_at)
        partial = len(recs) < len(b.employee_keys)
        self._repo.finish_batch(
            b.id, DONE, "partial" if partial else None,
            f"Оценки получены для {len(recs)} из {len(b.employee_keys)}" if partial else None)
        log.info("сопоставление: пакет собран", extra={
            "need_key": b.need_key, "task_id": b.task_id, "rated": len(recs),
            "of": len(b.employee_keys)})
        return True

    def get_state(self, need_key: str, *, limit: int, offset: int) -> MatchState:
        """Состояние прогона и список кандидатов из кэша (FR-036). Недопустимые для назначения
        статусы отсекаются на чтении: сотрудник мог сменить статус после расчёта."""
        need = self._needs.get_need(need_key)
        if need is None:
            raise MatchingError("need_not_found", "Потребность не найдена в кэше Jira")
        self._refresh(need_key)
        run = self._repo.get_run(need_key)
        batches = self._repo.list_batches(need_key)

        tasks: list[TaskInfo] = []
        awaiting = running = False
        for b in batches:
            tstatus, confirm = b.status, False
            if b.status == PENDING:
                view = self._gw.peek(b.task_id)
                tstatus = view.status if view else "failed"
                confirm = bool(view and view.requires_confirmation)
                awaiting = awaiting or confirm
                running = running or not confirm
            tasks.append(TaskInfo(b.task_id, tstatus, confirm, b.status, len(b.employee_keys)))
        n_done = sum(b.status == DONE for b in batches)
        n_failed = sum(b.status == FAILED for b in batches)
        if run is None:
            status = NONE
        elif awaiting and not running:
            status = AWAITING
        elif awaiting or running:
            status = RUNNING
        elif n_failed and not n_done:
            status = FAILED_RUN
        elif n_failed:
            status = PARTIAL
        else:
            status = DONE_RUN

        cands = self._repo.list_candidates(only_assignable=True)
        recs = self._repo.list_recommendations(need_key)
        model = run.model if run else None
        active_model = self._active_model()
        items: list[MatchItem] = []
        for c in cands:
            mine = [r for r in recs if r.employee_key == c.employee_key
                    and r.cv_file_id == c.file_id]
            if not mine:
                continue
            rec = next((r for r in mine if r.model == model), mine[0])  # свежие сверху
            stale = (
                rec.need_jira_updated_at != need.jira_updated_at
                or not _same(rec.note_snapshot_at, c.note_updated_at)
                or (active_model is not None and rec.model != active_model))
            items.append(MatchItem(
                c.employee_key, c.full_name, c.status, rec.match_percent, rec.rationale, c.note,
                c.note_author, c.note_updated_at, c.file_id, rec.created_at, stale, c.skills))
        items.sort(key=lambda i: (-i.percent, i.full_name, i.employee_key))
        calc = max((i.calculated_at for i in items), default=None)
        errors = sorted({b.error_message for b in batches
                         if b.status == FAILED and b.error_message})
        return MatchState(
            need_key=need_key, status=status, requested_by=run.requested_by if run else None,
            model=model, started_at=run.created_at if run else None, calculated_at=calc,
            stale=any(i.stale for i in items), candidates_total=len(cands),
            evaluated=len(items), total=len(items), items=items[offset:offset + limit],
            tasks=tasks, batches_total=len(batches), batches_done=n_done,
            batches_failed=n_failed, errors=errors)

    # ------------------------------------------------------------------ «Резюме» (FR-032, FR-003)

    def list_resume(self, **kw):
        return self._repo.list_resume(**kw)

    def technologies(self) -> list[tuple[str, int]]:
        return self._repo.list_technologies()

    def employee(self, employee_key: str):
        return self._repo.get_employee(employee_key)

    def skills_of(self, file_id: int) -> tuple[tuple[str, ...], str | None]:
        st = self._repo.get_skill_state(file_id)
        return self._repo.get_skills(file_id), st.status if st else None

    def _refresh(self, need_key: str) -> None:
        """Ленивый сбор: завершённые задачи пакетов превращаются в записи кэша."""
        for b in self._repo.list_batches(need_key):
            if b.status == PENDING:
                self._collect_batch(b)

    def _active_model(self) -> str | None:
        try:
            return self._gw.resolve_provider(None).model or None
        except Exception:  # noqa: BLE001 - нет провайдера: сравнение моделей пропускаем
            return None
