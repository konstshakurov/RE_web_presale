"""Модуль matching (KON-38): навыки из Основного CV (FR-027), сопоставление потребности с
кандидатами (FR-005, FR-036), профиль (FR-003), фильтр по технологии и поиск по смыслу (FR-032).

Все обращения к LLM идут только через шлюз ``llm_gateway`` (ADR-007): модуль ставит задачи
(``submit``) и забирает результат (``peek``), сам в сеть не ходит. Результат собирается двумя
путями, оба идемпотентны: worker после выполнения задачи (``collect_task``) и ленивый сбор при
запросе состояния. Тексты CV, заметки и ответы LLM в логи не попадают.

Оценка двухступенчатая (масштаб 200+ сотрудников). Ступень 1 - скрининг: LLM оценивает всех
допустимых кандидатов только по выделенным навыкам (пакеты по ~50, метки K1..Kn). Ступень 2 -
глубокая оценка по тексту CV: топ-N рейтинга скрининга, затем порциями «Оценить следующих».
Каждый прогон (одна ступень) - группа задач шлюза с общим ``run_id``: для внешнего провайдера
он показывается одним предпросмотром и подтверждается одним действием (FR-023).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from ..llm_gateway import AnonymizationError, GatewayError
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
    CHAIN_DONE,
    CHAIN_FORCE,
    CHAIN_PENDING,
    DONE,
    FAILED,
    PENDING,
    STAGE_DEEP,
    STAGE_SCREEN,
    BatchRow,
    CandRow,
    MatchRepo,
    NeedRow,
    NewBatch,
    NewRec,
    NewScreen,
    RecRow,
    RunRow,
    ScreenRow,
    SearchBatchRow,
    SearchResRow,
    SearchRunRow,
    SkillState,
)
from .screening import (
    DEEP_SIZE_DEFAULT,
    SCREEN_BATCH_DEFAULT,
    SCREEN_MAX_CHARS,
    SCREEN_SYSTEM,
    Prelim,
    ScreenCand,
    build_match_screen_prompt,
    build_search_screen_prompt,
    pick_next,
    pick_top,
    rank,
    screen_block,
    skills_hash,
)
from .selection import MAX_EVALUATED, need_text, split_batches
from .semantic import (
    MAX_QUERY_CHARS,
    MIN_QUERY_CHARS,
    RETENTION_DAYS,
    SEARCH_SYSTEM,
    Scored,
    build_search_prompt,
    fallback_rationale,
    query_hash,
    score_candidates,
    select_pool,
)
from .settings import StaticSettings

log = logging.getLogger(__name__)

PURPOSE_MATCH = "match"
PURPOSE_SKILLS = "skills"
PURPOSE_SEARCH = "search"
PURPOSE_MATCH_SCREEN = "match_screen"
PURPOSE_SEARCH_SCREEN = "search_screen"
PURPOSES = (PURPOSE_MATCH, PURPOSE_SKILLS, PURPOSE_SEARCH, PURPOSE_MATCH_SCREEN,
            PURPOSE_SEARCH_SCREEN)
BACKFILL_LIMIT = 200
#: Глубокая оценка: пакет - не более стольких кандидатов и символов
DEEP_BATCH_MAX = 5
DEEP_BATCH_CHARS = 24_000

#: Итоговый статус прогона
NONE, RUNNING, AWAITING, DONE_RUN, PARTIAL, FAILED_RUN = (
    "none", "running", "awaiting_confirmation", "done", "partial", "failed")
#: Поиск по смыслу: LLM недоступна (нет провайдера) - показано ранжирование без неё
UNAVAILABLE = "unavailable"
#: Ступень в ответе API
SCREENING, DEEP = "screening", "deep"
_STAGE_NAME = {STAGE_SCREEN: SCREENING, STAGE_DEEP: DEEP}


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
    def get(self, owner_login: str, task_id: str): ...
    def confirm_many(self, owner_login: str, task_ids: list[str]): ...
    def edit_prompt(self, owner_login: str, task_id: str, new_prompt: str): ...
    def cancel(self, owner_login: str, task_id: str): ...


@dataclass(frozen=True)
class TaskInfo:
    task_id: str
    status: str  # статус задачи шлюза либо done/failed по итогу сбора
    requires_confirmation: bool
    batch_status: str
    candidates: int
    stage: str = DEEP       # screening | deep
    run_id: str = ""        # группа для подтверждения: POST /matching/runs/{run_id}/confirm


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
    gaps: tuple[str, ...] | None = None  # None - не рассчитано (оценка до V010)


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
    evaluated: int             # кандидаты с глубокой оценкой в кэше
    total: int                 # в выдаче (=evaluated)
    items: list[MatchItem]
    tasks: list[TaskInfo]
    batches_total: int = 0
    batches_done: int = 0
    batches_failed: int = 0
    errors: list[str] = field(default_factory=list)
    stage: str | None = None   # ступень текущего прогона: screening | deep
    run_id: str | None = None  # текущий прогон (подтверждение внешнего провайдера)
    deep_size: int = 0         # размер порции глубокой оценки N (настройка администратора)
    remaining: int = 0         # допустимые, ещё не оценённые глубоко
    screened: int = 0          # с предварительным баллом скрининга LLM
    screen_total: int = 0      # допустимые с выделенными навыками (кандидаты скрининга)
    can_evaluate_next: bool = False


@dataclass(frozen=True)
class SkillsResult:
    employee_key: str
    outcome: str  # scheduled | exists | no_cv | no_provider | error
    task_id: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class SearchItem:
    employee_key: str
    full_name: str
    status: str
    source: str                # llm | screening | fallback
    percent: int | None        # только у source=llm
    rationale: str
    score: int                 # детерминированная оценка: технологии * 3 + слова запроса
    matched_skills: tuple[str, ...]
    matched_terms: tuple[str, ...]
    note: str | None
    note_author: str | None
    note_updated_at: datetime | None
    cv_file_id: int
    calculated_at: datetime | None
    stale: bool
    skills: tuple[str, ...]
    screen_percent: int | None = None  # предварительный балл скрининга LLM
    gaps: tuple[str, ...] | None = None  # только у source=llm; None - не рассчитано


@dataclass(frozen=True)
class SearchState:
    query: str
    status: str                # none | running | awaiting_confirmation | done | partial |
                               # failed | unavailable
    mode: str                  # llm - есть оценки LLM; fallback - только технологии и текст
    notice: str | None         # понятное сообщение о деградации
    requested_by: str | None
    model: str | None
    started_at: datetime | None
    calculated_at: datetime | None
    stale: bool
    candidates_total: int      # в области поиска (с учётом фильтров)
    evaluated: int             # с глубокой оценкой LLM
    total: int                 # в выдаче
    items: list[SearchItem]
    tasks: list[TaskInfo]
    batches_total: int = 0
    batches_done: int = 0
    batches_failed: int = 0
    errors: list[str] = field(default_factory=list)
    stage: str | None = None
    run_id: str | None = None
    deep_size: int = 0
    remaining: int = 0         # в выдаче без глубокой оценки
    screened: int = 0
    screen_total: int = 0
    can_evaluate_next: bool = False


@dataclass(frozen=True)
class SearchFilters:
    """Детерминированные фильтры «Резюме», сужающие область поиска по смыслу."""
    status: str | None = None
    tags: tuple[str, ...] = ()
    match_all: bool = True
    text: str | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "tags": list(self.tags), "match_all": self.match_all,
                "text": self.text}

    @staticmethod
    def from_dict(d: dict | None) -> SearchFilters:
        d = d or {}
        return SearchFilters(
            status=d.get("status") or None, tags=tuple(d.get("tags") or ()),
            match_all=bool(d.get("match_all", True)), text=d.get("text") or None)


@dataclass(frozen=True)
class RunTask:
    task_id: str
    status: str
    candidates: int
    requires_confirmation: bool
    system: str | None = None   # обезличенный текст внешней LLM (предпросмотр, FR-023)
    prompt: str | None = None
    replacements: dict[str, int] = field(default_factory=dict)
    ner_used: bool = False
    error_message: str | None = None
    editable: bool = False      # внешняя задача до подтверждения: текст можно править
    edited: bool = False        # текст правил пользователь


@dataclass(frozen=True)
class RunInfo:
    """Прогон (одна ступень) целиком: предпросмотр всех пакетов и подтверждение разом."""
    run_id: str
    kind: str                  # match | search
    stage: str                 # screening | deep
    status: str                # awaiting_confirmation | running | done | partial | failed
    requires_confirmation: bool
    is_external: bool
    provider_name: str
    candidates: int
    tasks: list[RunTask]
    replacements: dict[str, int]
    need_key: str | None = None


_ANON_FAILED = "Не удалось надёжно обезличить текст для внешней LLM; отправка отменена"
_EPOCH = datetime(1970, 1, 1)  # created_at прогона проставляет БД; значение не используется


def _search_notice(status: str, mode: str, errors: list[str]) -> str | None:
    """Понятное сообщение, когда ранжирование LLM недоступно или неполно."""
    tail = "показано ранжирование по технологиям и тексту CV."
    why = f" ({'; '.join(errors)})" if errors else ""
    if status == UNAVAILABLE:
        return f"Поиск по смыслу (LLM) недоступен: провайдер LLM не настроен; {tail}"
    if status == FAILED_RUN:
        if mode == "llm":
            return (f"Обновить оценки LLM не удалось{why}. Показаны сохранённые оценки, "
                    "остальные - по технологиям и тексту CV.")
        return f"Ранжирование LLM не выполнено{why}; {tail}"
    if status == PARTIAL:
        return (f"LLM оценила не всех кандидатов{why}; остальные показаны по технологиям "
                "и тексту CV.")
    if status == RUNNING:
        return f"Ранжирование LLM выполняется; пока {tail}"
    if status == AWAITING:
        return ("Запрос ждёт подтверждения отправки во внешнюю LLM (предпросмотр обезличенного "
                f"текста, FR-023); пока {tail}")
    if status == NONE and mode == "fallback":
        return f"Ранжирование LLM не запускалось; {tail}"
    return None


def _label_map(keys: tuple[str, ...]) -> dict[str, str]:
    return {f"K{i + 1}": k for i, k in enumerate(keys)}


def _same(a: datetime | None, b: datetime | None) -> bool:
    return a == b


class MatchingService:
    def __init__(
        self, repo: MatchRepo, needs: NeedSource, gateway: Gateway,
        *, max_evaluated: int = MAX_EVALUATED, settings=None,
    ) -> None:
        self._repo = repo
        self._needs = needs
        self._gw = gateway
        # settings: deep_size() / screen_batch() / set(); без него - значения по умолчанию
        self._settings = settings or StaticSettings(
            max_evaluated if max_evaluated else DEEP_SIZE_DEFAULT, SCREEN_BATCH_DEFAULT)

    # ------------------------------------------------------------------ настройки

    def settings(self) -> tuple[int, int]:
        """(размер глубокой оценки N, размер пакета скрининга)."""
        return self._settings.deep_size(), self._settings.screen_batch()

    def update_settings(self, deep_size: int | None, screen_batch: int | None) -> tuple[int, int]:
        try:
            self._settings.set(deep_size, screen_batch)
        except ValueError as exc:
            raise MatchingError("bad_settings", str(exc)) from None
        return self.settings()

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

    # ------------------------------------------------------------------ общее для прогонов

    def _post_batches(
        self, *, login: str, purpose: str, system: str, provider_id: int, ref: str | None,
        run_id: str, stage: str, groups: list[list[int]], items: list[CandRow], build,
    ) -> list[NewBatch]:
        """По задаче шлюза на группу кандидатов. При сбое уже поставленные задачи отменяются:
        задач без прогона не остаётся."""
        submitted: list[str] = []
        out: list[NewBatch] = []
        try:
            for idx in groups:
                chunk = [items[i] for i in idx]
                view = self._gw.submit(
                    login, purpose, build(chunk), system=system, provider_id=provider_id,
                    ref=ref)
                submitted.append(view.id)
                out.append(NewBatch(
                    view.id, tuple(c.employee_key for c in chunk),
                    tuple(c.file_id for c in chunk), stage, run_id))
        except Exception:
            for tid in submitted:
                try:
                    self._gw.cancel(login, tid)
                except Exception:  # noqa: BLE001
                    pass
            raise
        return out

    @staticmethod
    def _screen_map(
        cands: list[CandRow], screens: list[ScreenRow], model: str, need: NeedRow | None = None,
    ) -> dict[str, int]:
        """Ключ сотрудника -> свежий балл скрининга: тот же файл, модель и набор навыков (и та
        же версия потребности для сопоставления). ``screens`` - свежие сверху."""
        by_file: dict[int, ScreenRow] = {}
        for r in screens:
            if r.model == model:
                by_file.setdefault(r.cv_file_id, r)
        out: dict[str, int] = {}
        for c in cands:
            r = by_file.get(c.file_id)
            if (r is not None and r.employee_key == c.employee_key
                    and r.skills_hash == skills_hash(c.skills)
                    and (need is None or r.need_jira_updated_at == need.jira_updated_at)):
                out[c.employee_key] = r.match_percent
        return out

    def _collect_screen(self, b, view, label_to_key, recs_out: list[NewScreen]) -> None:
        """Балл скрининга по каждой метке ответа; хэш навыков берётся по текущему файлу."""
        results = parse_match(view.result or "", label_to_key)
        skills = self._repo.get_skills_many(list(b.file_ids))
        for key, file_id in zip(b.employee_keys, b.file_ids, strict=True):
            res = results.get(key)
            if res is not None:
                recs_out.append(NewScreen(
                    key, file_id, res.percent, skills_hash(skills.get(file_id, ()))))

    # ------------------------------------------------------------------ сопоставление

    def start_match(
        self, need_key: str, login: str, *, provider_id: int | None = None, force: bool = False,
    ) -> MatchState:
        """«Сопоставить» / «Пересчитать». До N допустимых кандидатов - сразу глубокая оценка всех.
        Больше - ступень 1 (скрининг всех по навыкам), по её завершении ступень 2 (топ-N)."""
        need = self._needs.get_need(need_key)
        if need is None:
            raise MatchingError("need_not_found", "Потребность не найдена в кэше Jira")
        provider = self._gw.resolve_provider(provider_id)  # GatewayError - как есть
        self._refresh(need_key)
        cands = self._repo.list_candidates(only_assignable=True)
        n = self._settings.deep_size()

        if len(cands) <= n:
            recs = self._repo.list_recommendations(need_key)
            todo = cands if force else [
                c for c in cands if not self._is_fresh(c, need, provider.model, recs)]
            if todo:
                self._submit_match(need, login, provider.id, provider.model, todo,
                                   stage=STAGE_DEEP, chain_state=CHAIN_DONE)
            else:
                log.info("сопоставление: всё в кэше", extra={"need_key": need_key})
            return self.get_state(need_key, limit=10, offset=0)

        have = self._screen_map(
            cands, self._repo.list_match_screens(need_key), provider.model, need)
        screenable = [c for c in cands if c.skills]
        todo_screen = screenable if force else [
            c for c in screenable if c.employee_key not in have]
        if todo_screen:
            self._submit_match(
                need, login, provider.id, provider.model, todo_screen, stage=STAGE_SCREEN,
                chain_state=CHAIN_FORCE if force else CHAIN_PENDING)
        else:
            todo = self._plan_match_top(need, cands, provider.model, force, n)
            if todo:
                self._submit_match(need, login, provider.id, provider.model, todo,
                                   stage=STAGE_DEEP, chain_state=CHAIN_DONE)
            else:
                log.info("сопоставление: всё в кэше", extra={"need_key": need_key})
        return self.get_state(need_key, limit=10, offset=0)

    def evaluate_next(
        self, need_key: str, login: str, *, provider_id: int | None = None,
    ) -> MatchState:
        """«Оценить следующих»: глубокая оценка следующей порции (N) рейтинга скрининга, у кого
        нет свежей оценки; уже оценённые из кэша не пересчитываются. Отдельный прогон -
        отдельное подтверждение у внешнего провайдера."""
        need = self._needs.get_need(need_key)
        if need is None:
            raise MatchingError("need_not_found", "Потребность не найдена в кэше Jira")
        provider = self._gw.resolve_provider(provider_id)
        self._refresh(need_key)
        cands = self._repo.list_candidates(only_assignable=True)
        recs = self._repo.list_recommendations(need_key)
        ranked = self._rank_match(
            need, cands, provider.model, self._repo.list_match_screens(need_key))
        fresh = {c.employee_key for c in cands
                 if self._is_fresh(c, need, provider.model, recs)}
        keys = pick_next(ranked, fresh, self._settings.deep_size())
        by_key = {c.employee_key: c for c in cands}
        todo = [by_key[k] for k in keys]
        if todo:
            self._submit_match(need, login, provider.id, provider.model, todo,
                               stage=STAGE_DEEP, chain_state=CHAIN_DONE)
        return self.get_state(need_key, limit=10, offset=0)

    @staticmethod
    def _is_fresh(c: CandRow, need: NeedRow, model: str, recs: list[RecRow]) -> bool:
        return any(
            r.employee_key == c.employee_key and r.cv_file_id == c.file_id and r.model == model
            and r.need_jira_updated_at == need.jira_updated_at
            and _same(r.note_snapshot_at, c.note_updated_at)
            for r in recs)

    def _rank_match(
        self, need: NeedRow, cands: list[CandRow], model: str, screens: list[ScreenRow],
    ) -> list[Prelim]:
        """Рейтинг ступени 1: балл скрининга LLM, у остальных - детерминированный по навыкам
        и тексту CV (кандидат без навыков не выпадает из рейтинга)."""
        screened = self._screen_map(cands, screens, model, need)
        lacking = [c.file_id for c in cands if c.employee_key not in screened]
        texts = self._repo.get_cv_texts(lacking)
        qtext = need_text(need.title, need.description, need.requirements_text, need.tags)
        return rank(
            [(c.employee_key, c.full_name, c.skills,
              "" if c.employee_key in screened else texts.get(c.file_id, "")) for c in cands],
            qtext, screened)

    def _plan_match_top(
        self, need: NeedRow, cands: list[CandRow], model: str, force: bool, n: int,
    ) -> list[CandRow]:
        """Ступень 2 после скрининга: первые N позиций рейтинга без свежих оценок кэша."""
        ranked = self._rank_match(
            need, cands, model, self._repo.list_match_screens(need.jira_key))
        recs = self._repo.list_recommendations(need.jira_key)
        fresh = set() if force else {
            c.employee_key for c in cands if self._is_fresh(c, need, model, recs)}
        by_key = {c.employee_key: c for c in cands}
        return [by_key[k] for k in pick_top(ranked, fresh, n)]

    def _submit_match(
        self, need: NeedRow, login: str, provider_id: int, model: str, todo: list[CandRow],
        *, stage: str, chain_state: str, expect_chain: bool = False,
    ) -> bool:
        """Поставить прогон ступени ``stage``: задачи шлюза по пакетам и запись прогона в одной
        транзакции под блокировкой потребности. False - прогон уже идёт (или решение о ступени 2
        уже принято другим сборщиком) - второй не запускаем."""
        run_id = str(uuid.uuid4())
        nt = NeedText(need.title, need.description, need.requirements_text)
        if stage == STAGE_SCREEN:
            sizes = [len(screen_block(ScreenCand("K0", c.employee_key, c.skills)))
                     for c in todo]
            groups = split_batches(sizes, max_items=self._settings.screen_batch(),
                                   max_chars=SCREEN_MAX_CHARS)
            purpose, system = PURPOSE_MATCH_SCREEN, SCREEN_SYSTEM

            def build(chunk: list[CandRow]) -> str:
                return build_match_screen_prompt(nt, [
                    ScreenCand(f"K{i + 1}", c.employee_key, c.skills)
                    for i, c in enumerate(chunk)])
        else:
            texts = self._repo.get_cv_texts([c.file_id for c in todo])
            sizes = [len(candidate_block(self._cand("K0", c, texts.get(c.file_id, ""))))
                     for c in todo]
            groups = split_batches(sizes, max_items=DEEP_BATCH_MAX, max_chars=DEEP_BATCH_CHARS)
            purpose, system = PURPOSE_MATCH, MATCH_SYSTEM

            def build(chunk: list[CandRow]) -> str:
                return build_match_prompt(nt, [
                    self._cand(f"K{i + 1}", c, texts.get(c.file_id, ""))
                    for i, c in enumerate(chunk)])

        with self._repo.run_tx(need.jira_key) as tx:
            if expect_chain:
                cur = tx.run()
                if cur is None or cur.chain_state == CHAIN_DONE:
                    return False
            if any(b.status == PENDING for b in tx.batches()):
                return False
            new_batches = self._post_batches(
                login=login, purpose=purpose, system=system, provider_id=provider_id,
                ref=need.jira_key, run_id=run_id, stage=stage, groups=groups, items=todo,
                build=build)
            tx.replace(
                RunRow(need.jira_key, login, provider_id, model, need.jira_updated_at,
                       need.jira_updated_at, chain_state),
                new_batches)
        log.info("сопоставление: прогон создан", extra={
            "need_key": need.jira_key, "stage": _STAGE_NAME[stage], "run_id": run_id,
            "batches": len(groups), "candidates": len(todo)})
        return True

    @staticmethod
    def _cand(label: str, c: CandRow, text: str) -> Candidate:
        return Candidate(label, c.employee_key, c.skills, text, c.note, c.extra_experience)

    def collect_task(self, task_id: str) -> bool:
        """Подобрать результат завершённой задачи шлюза (worker и ленивый сбор). True, если
        задача принадлежит модулю и обработана."""
        batch = self._repo.batch_by_task(task_id)
        if batch is not None:
            done = self._collect_batch(batch)
            self._advance_match(batch.need_key)
            return done
        sb = self._repo.search_batch_by_task(task_id)
        if sb is not None:
            done = self._collect_search_batch(sb)
            self._advance_search(sb.requested_by, sb.query_hash)
            return done
        st = self._repo.state_by_task(task_id)
        if st is not None:
            self._collect_skills(st)
            return True
        return False

    def _failed_batch(self, b, view, finish) -> bool | None:
        """Общая часть сбора пакета: потеря, отказ, ещё идёт. True - обработан; None - дальше."""
        if view is None:
            finish(b.id, FAILED, "task_lost", "Задача LLM не найдена")
            return True
        if view.status in ("failed", "timeout", "cancelled"):
            finish(b.id, FAILED, view.error_code or view.status,
                   view.error_message or "Задача LLM не выполнена")
            return True
        if view.status != "done":
            return False
        return None

    def _collect_batch(self, b: BatchRow) -> bool:
        if b.status != PENDING:
            return False
        view = self._gw.peek(b.task_id)
        early = self._failed_batch(b, view, self._repo.finish_batch)
        if early is not None:
            return early
        run = self._repo.get_run(b.need_key)
        if run is None:
            return False
        try:
            if b.stage == STAGE_SCREEN:
                screens: list[NewScreen] = []
                self._collect_screen(b, view, _label_map(b.employee_keys), screens)
                if not screens:
                    raise ResponseError("в ответе нет оценок кандидатов")
                self._repo.upsert_match_screens(
                    b.need_key, screens, model=run.model,
                    need_updated_at=run.need_jira_updated_at)
                n_rated = len(screens)
            else:
                results = parse_match(view.result or "", _label_map(b.employee_keys))
                recs: list[NewRec] = []
                for key, file_id in zip(b.employee_keys, b.file_ids, strict=True):
                    res = results.get(key)
                    if res is None:
                        continue
                    emp = self._repo.get_employee(key)
                    snap = emp.note_updated_at if emp else None
                    if snap is not None and snap > view.created_at:
                        snap = view.created_at  # заметка правилась после постановки: кэш устарел
                    recs.append(NewRec(key, file_id, res.percent, res.rationale, snap, res.gaps))
                self._repo.upsert_recommendations(
                    b.need_key, recs, model=run.model, need_updated_at=run.need_jira_updated_at)
                n_rated = len(recs)
        except ResponseError as exc:
            self._repo.finish_batch(
                b.id, FAILED, "bad_response", f"Ответ LLM не разобран: {exc}")
            return True
        partial = n_rated < len(b.employee_keys)
        self._repo.finish_batch(
            b.id, DONE, "partial" if partial else None,
            f"Оценки получены для {n_rated} из {len(b.employee_keys)}" if partial else None)
        log.info("сопоставление: пакет собран", extra={
            "need_key": b.need_key, "task_id": b.task_id, "stage": _STAGE_NAME[b.stage],
            "rated": n_rated, "of": len(b.employee_keys)})
        return True

    def _advance_match(self, need_key: str) -> None:
        """Скрининг завершён - поставить ступень 2 (топ-N). Идемпотентно: решение принимается
        один раз под блокировкой, повторные вызовы worker и API ничего не делают."""
        run = self._repo.get_run(need_key)
        if run is None or run.chain_state == CHAIN_DONE:
            return
        batches = self._repo.list_batches(need_key)
        if any(b.status == PENDING for b in batches):
            return
        screen = [b for b in batches if b.stage == STAGE_SCREEN]
        need = self._needs.get_need(need_key)
        todo: list[CandRow] = []
        if screen and need is not None and not all(b.status == FAILED for b in screen):
            try:
                provider = self._gw.resolve_provider(run.provider_id)
                cands = self._repo.list_candidates(only_assignable=True)
                todo = self._plan_match_top(
                    need, cands, provider.model, run.chain_state == CHAIN_FORCE,
                    self._settings.deep_size())
                if todo and self._submit_match(
                        need, run.requested_by, provider.id, provider.model, todo,
                        stage=STAGE_DEEP, chain_state=CHAIN_DONE, expect_chain=True):
                    return
            except (GatewayError, AnonymizationError) as exc:
                # ступень 2 не поставлена; скрининг сохранён, «Оценить следующих» покажет причину
                log.warning("сопоставление: ступень 2 не поставлена", extra={
                    "need_key": need_key, "error": getattr(exc, "code", type(exc).__name__)})
        with self._repo.run_tx(need_key) as tx:
            tx.finish_chain()

    def get_state(self, need_key: str, *, limit: int, offset: int) -> MatchState:
        """Состояние прогона и список кандидатов из кэша (FR-036). Недопустимые для назначения
        статусы отсекаются на чтении: сотрудник мог сменить статус после расчёта."""
        need = self._needs.get_need(need_key)
        if need is None:
            raise MatchingError("need_not_found", "Потребность не найдена в кэше Jira")
        self._refresh(need_key)
        run = self._repo.get_run(need_key)
        batches = self._repo.list_batches(need_key)

        tasks, status, n_done, n_failed = self._overview(
            batches, has_run=run is not None,
            chain_pending=run is not None and run.chain_state != CHAIN_DONE)

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
                c.note_author, c.note_updated_at, c.file_id, rec.created_at, stale, c.skills,
                None if rec.gaps is None else tuple(rec.gaps)))
        items.sort(key=lambda i: (-i.percent, i.full_name, i.employee_key))
        calc = max((i.calculated_at for i in items), default=None)
        errors = sorted({b.error_message for b in batches
                         if b.status == FAILED and b.error_message})
        screens = self._repo.list_match_screens(need_key)
        screened = self._screen_map(cands, screens, model or active_model or "", need)
        running = any(b.status == PENDING for b in batches)
        can_next = (
            active_model is not None and not running and any(
                not self._is_fresh(c, need, active_model, recs) for c in cands))
        return MatchState(
            need_key=need_key, status=status, requested_by=run.requested_by if run else None,
            model=model, started_at=run.created_at if run else None, calculated_at=calc,
            stale=any(i.stale for i in items), candidates_total=len(cands),
            evaluated=len(items), total=len(items), items=items[offset:offset + limit],
            tasks=tasks, batches_total=len(batches), batches_done=n_done,
            batches_failed=n_failed, errors=errors,
            stage=_STAGE_NAME.get(batches[-1].stage) if batches else None,
            run_id=batches[-1].run_id or None if batches else None,
            deep_size=self._settings.deep_size(), remaining=len(cands) - len(items),
            screened=len(screened), screen_total=sum(1 for c in cands if c.skills),
            can_evaluate_next=can_next)

    # ------------------------------------------------------------------ поиск по смыслу (FR-032)

    @staticmethod
    def clean_query(query: str) -> str:
        q = re.sub(r"\s+", " ", (query or "").strip())
        if len(q) < MIN_QUERY_CHARS:
            raise MatchingError("bad_query", f"Запрос короче {MIN_QUERY_CHARS} символов")
        if len(q) > MAX_QUERY_CHARS:
            raise MatchingError("bad_query", f"Запрос длиннее {MAX_QUERY_CHARS} символов")
        return q

    def start_search(
        self, login: str, query: str, *, provider_id: int | None = None, force: bool = False,
        filters: SearchFilters | None = None, limit: int = 10, offset: int = 0,
    ) -> SearchState:
        """«Применить» с запросом по смыслу. Область поиска до N кандидатов - сразу глубокая
        оценка отобранных по технологиям и тексту. Больше - ступень 1 (скрининг всех по навыкам),
        затем ступень 2 (топ-N). Нет провайдера или задачу не поставить - ранжирование без LLM
        с сообщением."""
        f = filters or SearchFilters()
        q = self.clean_query(query)
        h = query_hash(q)
        self._repo.purge_search(RETENTION_DAYS)
        try:
            provider = self._gw.resolve_provider(provider_id)
        except GatewayError:
            if provider_id is not None:
                raise  # явно выбранный провайдер не найден - ошибка вызова, не деградация
            provider = None
        failure: str | None = None
        if provider is not None:
            try:
                self._launch_search(login, q, h, provider, f, force)
            except (GatewayError, AnonymizationError) as exc:
                failure = _ANON_FAILED if isinstance(exc, AnonymizationError) else exc.message
                log.warning("поиск по смыслу: задача не поставлена", extra={
                    "error": getattr(exc, "code", type(exc).__name__)})
        return self.get_search(login, q, filters=f, limit=limit, offset=offset, failure=failure)

    def _launch_search(self, login, q, h, provider, f: SearchFilters, force: bool) -> None:
        cands, ranked, pool, texts = self._search_scope(q, f)
        by_key = {c.employee_key: c for c in cands}
        results = self._repo.list_search_results(h)
        n = self._settings.deep_size()
        if len(cands) <= n:
            order = [by_key[s.key] for s in pool]
            todo = order if force else [
                c for c in order if not self._search_fresh(c, provider.model, results)]
            if todo:
                self._submit_search(login, q, h, provider.id, provider.model, todo, texts, f,
                                    stage=STAGE_DEEP, chain_state=CHAIN_DONE)
            else:
                log.info("поиск по смыслу: всё в кэше")
            return
        have = self._screen_map(cands, self._repo.list_search_screens(h), provider.model)
        todo_screen = [c for c in cands if c.skills and (force or c.employee_key not in have)]
        if todo_screen:
            self._submit_search(
                login, q, h, provider.id, provider.model, todo_screen, texts, f,
                stage=STAGE_SCREEN, chain_state=CHAIN_FORCE if force else CHAIN_PENDING)
            return
        todo = self._plan_search_top(q, h, cands, texts, provider.model, force, n, results)
        if todo:
            self._submit_search(login, q, h, provider.id, provider.model, todo, texts, f,
                                stage=STAGE_DEEP, chain_state=CHAIN_DONE)
        else:
            log.info("поиск по смыслу: всё в кэше")

    def evaluate_next_search(
        self, login: str, query: str, *, provider_id: int | None = None,
        filters: SearchFilters | None = None, limit: int = 10, offset: int = 0,
    ) -> SearchState:
        """«Оценить следующих» в поиске: следующая порция (N) рейтинга без свежей оценки LLM.
        Отдельный прогон - отдельное подтверждение у внешнего провайдера."""
        f = filters or SearchFilters()
        q = self.clean_query(query)
        h = query_hash(q)
        provider = self._gw.resolve_provider(provider_id)  # нет провайдера - ошибка вызова
        self._refresh_search(login, h)
        failure: str | None = None
        try:
            cands, _, _, texts = self._search_scope(q, f)
            by_key = {c.employee_key: c for c in cands}
            results = self._repo.list_search_results(h)
            prelim = self._search_prelim(
                q, cands, texts, self._repo.list_search_screens(h), provider.model)
            visible = self._visible(prelim, self._rated_keys(cands, results))
            fresh = {c.employee_key for c in cands
                     if self._search_fresh(c, provider.model, results)}
            keys = pick_next(visible, fresh, self._settings.deep_size())
            todo = [by_key[k] for k in keys]
            if todo:
                self._submit_search(login, q, h, provider.id, provider.model, todo, texts, f,
                                    stage=STAGE_DEEP, chain_state=CHAIN_DONE)
        except (GatewayError, AnonymizationError) as exc:
            failure = _ANON_FAILED if isinstance(exc, AnonymizationError) else exc.message
        return self.get_search(login, q, filters=f, limit=limit, offset=offset, failure=failure)

    @staticmethod
    def _search_fresh(c: CandRow, model: str, results: list[SearchResRow]) -> bool:
        return any(r.cv_file_id == c.file_id and r.model == model
                   and _same(r.note_snapshot_at, c.note_updated_at) for r in results)

    @staticmethod
    def _rated_keys(cands: list[CandRow], results: list[SearchResRow]) -> set[str]:
        """Кандидаты с глубокой оценкой по текущему файлу (любой модели)."""
        files = {r.cv_file_id for r in results}
        return {c.employee_key for c in cands if c.file_id in files}

    def _search_prelim(
        self, q: str, cands: list[CandRow], texts: dict[int, str], screens: list[ScreenRow],
        model: str,
    ) -> list[Prelim]:
        screened = self._screen_map(cands, screens, model)
        return rank(
            [(c.employee_key, c.full_name, c.skills,
              "" if c.employee_key in screened else texts.get(c.file_id, "")) for c in cands],
            q, screened)

    @staticmethod
    def _visible(prelim: list[Prelim], rated: set[str]) -> list[Prelim]:
        """Кандидаты выдачи: с совпадениями (скрининг или детерминированный балл > 0) либо уже
        оценённые; если совпадений нет ни у кого - все (ранжирование оставлено LLM)."""
        if not any(p.percent > 0 for p in prelim):
            return list(prelim)
        return [p for p in prelim if p.percent > 0 or p.key in rated]

    def _plan_search_top(
        self, q: str, h: str, cands: list[CandRow], texts: dict[int, str], model: str,
        force: bool, n: int, results: list[SearchResRow],
    ) -> list[CandRow]:
        prelim = self._search_prelim(q, cands, texts, self._repo.list_search_screens(h), model)
        visible = [p for p in prelim if p.percent > 0] or prelim
        by_key = {c.employee_key: c for c in cands}
        fresh = set() if force else {
            c.employee_key for c in cands if self._search_fresh(c, model, results)}
        return [by_key[k] for k in pick_top(visible, fresh, n)]

    def _search_scope(
        self, query: str, f: SearchFilters,
    ) -> tuple[list[CandRow], list[Scored], list[Scored], dict[int, str]]:
        """Область поиска (допустимые статусы либо явно выбранный статус, основное CV с
        текстом, фильтры «Технология» и «Поиск») и ранжирование по технологиям и тексту."""
        cands = self._repo.list_candidates(only_assignable=not f.status)
        if f.status:
            cands = [c for c in cands if c.status == f.status]
        if f.tags:
            want = set(f.tags)
            cands = [c for c in cands if (want <= set(c.skills) if f.match_all
                                          else want & set(c.skills))]
        texts = self._repo.get_cv_texts([c.file_id for c in cands])
        if f.text:
            low = f.text.lower()
            cands = [c for c in cands if low in " ".join(
                (c.full_name, c.employee_key, c.file_name, texts.get(c.file_id, ""))).lower()]
        ranked = score_candidates(
            [(c.employee_key, c.full_name, c.skills, texts.get(c.file_id, "")) for c in cands],
            query)
        return cands, ranked, select_pool(ranked), texts

    def _submit_search(
        self, login: str, query: str, h: str, provider_id: int, model: str,
        todo: list[CandRow], texts: dict[int, str], f: SearchFilters,
        *, stage: str, chain_state: str, expect_chain: bool = False,
    ) -> bool:
        run_id = str(uuid.uuid4())
        if stage == STAGE_SCREEN:
            sizes = [len(screen_block(ScreenCand("K0", c.employee_key, c.skills)))
                     for c in todo]
            groups = split_batches(sizes, max_items=self._settings.screen_batch(),
                                   max_chars=SCREEN_MAX_CHARS)
            purpose, system = PURPOSE_SEARCH_SCREEN, SCREEN_SYSTEM

            def build(chunk: list[CandRow]) -> str:
                return build_search_screen_prompt(query, [
                    ScreenCand(f"K{i + 1}", c.employee_key, c.skills)
                    for i, c in enumerate(chunk)])
        else:
            sizes = [len(candidate_block(self._cand("K0", c, texts.get(c.file_id, ""))))
                     for c in todo]
            groups = split_batches(sizes, max_items=DEEP_BATCH_MAX, max_chars=DEEP_BATCH_CHARS)
            purpose, system = PURPOSE_SEARCH, SEARCH_SYSTEM

            def build(chunk: list[CandRow]) -> str:
                return build_search_prompt(query, [
                    self._cand(f"K{i + 1}", c, texts.get(c.file_id, ""))
                    for i, c in enumerate(chunk)])

        with self._repo.search_tx(login, h) as tx:
            if expect_chain:
                cur = tx.run()
                if cur is None or cur.chain_state == CHAIN_DONE:
                    return False
            if any(b.status == PENDING for b in tx.batches()):
                return False  # прогон уже идёт: второй не запускаем
            new_batches = self._post_batches(
                login=login, purpose=purpose, system=system, provider_id=provider_id, ref=None,
                run_id=run_id, stage=stage, groups=groups, items=todo, build=build)
            tx.replace(
                SearchRunRow(login, h, query, provider_id, model, _EPOCH, chain_state,
                             f.to_dict()),
                new_batches)
        log.info("поиск по смыслу: прогон создан", extra={
            "stage": _STAGE_NAME[stage], "run_id": run_id, "batches": len(groups),
            "candidates": len(todo)})
        return True

    def _collect_search_batch(self, b: SearchBatchRow) -> bool:
        if b.status != PENDING:
            return False
        view = self._gw.peek(b.task_id)
        early = self._failed_batch(b, view, self._repo.finish_search_batch)
        if early is not None:
            return early
        run = self._repo.get_search_run(b.requested_by, b.query_hash)
        if run is None:
            return False
        try:
            if b.stage == STAGE_SCREEN:
                screens: list[NewScreen] = []
                self._collect_screen(b, view, _label_map(b.employee_keys), screens)
                self._repo.upsert_search_screens(b.query_hash, screens, model=run.model)
                n_rated = len(screens)
            else:
                results = parse_match(view.result or "", _label_map(b.employee_keys))
                recs: list[NewRec] = []
                for key, file_id in zip(b.employee_keys, b.file_ids, strict=True):
                    res = results.get(key)
                    if res is None:
                        continue
                    emp = self._repo.get_employee(key)
                    snap = emp.note_updated_at if emp else None
                    if snap is not None and snap > view.created_at:
                        snap = view.created_at  # заметка правилась после постановки
                    recs.append(NewRec(key, file_id, res.percent, res.rationale, snap, res.gaps))
                self._repo.upsert_search_results(b.query_hash, recs, model=run.model)
                n_rated = len(recs)
        except ResponseError as exc:
            self._repo.finish_search_batch(
                b.id, FAILED, "bad_response", f"Ответ LLM не разобран: {exc}")
            return True
        partial = n_rated < len(b.employee_keys)
        self._repo.finish_search_batch(
            b.id, DONE, "partial" if partial else None,
            f"Оценки получены для {n_rated} из {len(b.employee_keys)}" if partial else None)
        log.info("поиск по смыслу: пакет собран", extra={
            "task_id": b.task_id, "stage": _STAGE_NAME[b.stage], "rated": n_rated,
            "of": len(b.employee_keys)})
        return True

    def _advance_search(self, login: str, h: str) -> None:
        """Скрининг поиска завершён - поставить ступень 2 (топ-N) той же области поиска."""
        run = self._repo.get_search_run(login, h)
        if run is None or run.chain_state == CHAIN_DONE:
            return
        batches = self._repo.list_search_batches(login, h)
        if any(b.status == PENDING for b in batches):
            return
        screen = [b for b in batches if b.stage == STAGE_SCREEN]
        if screen and not all(b.status == FAILED for b in screen):
            try:
                provider = self._gw.resolve_provider(run.provider_id)
                f = SearchFilters.from_dict(run.scope)
                cands, _, _, texts = self._search_scope(run.query_text, f)
                todo = self._plan_search_top(
                    run.query_text, h, cands, texts, provider.model,
                    run.chain_state == CHAIN_FORCE, self._settings.deep_size(),
                    self._repo.list_search_results(h))
                if todo and self._submit_search(
                        login, run.query_text, h, provider.id, provider.model, todo, texts, f,
                        stage=STAGE_DEEP, chain_state=CHAIN_DONE, expect_chain=True):
                    return
            except (GatewayError, AnonymizationError) as exc:
                log.warning("поиск по смыслу: ступень 2 не поставлена", extra={
                    "error": getattr(exc, "code", type(exc).__name__)})
        with self._repo.search_tx(login, h) as tx:
            tx.finish_chain()

    def _refresh_search(self, login: str, h: str) -> None:
        for b in self._repo.list_search_batches(login, h):
            if b.status == PENDING:
                self._collect_search_batch(b)
        self._advance_search(login, h)

    def get_search(
        self, login: str, query: str, *, filters: SearchFilters | None = None, limit: int,
        offset: int, failure: str | None = None,
    ) -> SearchState:
        """Состояние прогона и список: оценённые LLM по проценту, затем остальные по предварительной
        оценке скрининга либо по технологиям и тексту. Без LLM список строится так же,
        ``notice`` объясняет причину."""
        f = filters or SearchFilters()
        q = self.clean_query(query)
        h = query_hash(q)
        self._refresh_search(login, h)
        run = self._repo.get_search_run(login, h)
        batches = self._repo.list_search_batches(login, h)
        tasks, status, n_done, n_failed = self._overview(
            batches, has_run=run is not None,
            chain_pending=run is not None and run.chain_state != CHAIN_DONE)
        active_model = self._active_model()
        errors = sorted({b.error_message for b in batches
                         if b.status == FAILED and b.error_message})
        if failure:
            status, errors = FAILED_RUN, [*errors, failure]
        elif run is None and active_model is None:
            status = UNAVAILABLE

        cands, ranked, _, texts = self._search_scope(q, f)
        by_key = {c.employee_key: c for c in cands}
        det = {s.key: s for s in ranked}
        model = run.model if run else active_model
        by_file: dict[int, SearchResRow] = {}
        results = self._repo.list_search_results(h)
        for r in results:  # свежие сверху
            cur = by_file.get(r.cv_file_id)
            if cur is None or (r.model == model and cur.model != model):
                by_file[r.cv_file_id] = r
        prelim = self._search_prelim(
            q, cands, texts, self._repo.list_search_screens(h), model or "")
        screened = {p.key: p.percent for p in prelim if p.source == "screen"}
        rated_keys = {c.employee_key for c in cands
                      if (r := by_file.get(c.file_id)) is not None
                      and r.employee_key == c.employee_key}
        visible = self._visible(prelim, rated_keys)
        rated: list[SearchItem] = []
        rest: list[SearchItem] = []
        for p in visible:
            c = by_key[p.key]
            s = det[p.key]
            r = by_file.get(c.file_id)
            if r is not None and r.employee_key == c.employee_key:
                stale = (not _same(r.note_snapshot_at, c.note_updated_at)
                         or (active_model is not None and r.model != active_model))
                rated.append(SearchItem(
                    c.employee_key, c.full_name, c.status, "llm", r.match_percent, r.rationale,
                    s.score, s.skills, s.terms, c.note, c.note_author, c.note_updated_at,
                    c.file_id, r.created_at, stale, c.skills, screened.get(p.key),
                    None if r.gaps is None else tuple(r.gaps)))
            elif p.source == "screen":
                rest.append(SearchItem(
                    c.employee_key, c.full_name, c.status, "screening", None,
                    f"Предварительная оценка по навыкам: {p.percent}%.", s.score, s.skills,
                    s.terms, c.note, c.note_author, c.note_updated_at, c.file_id, None, False,
                    c.skills, p.percent))
            else:
                rest.append(SearchItem(
                    c.employee_key, c.full_name, c.status, "fallback", None,
                    fallback_rationale(s), s.score, s.skills, s.terms, c.note, c.note_author,
                    c.note_updated_at, c.file_id, None, False, c.skills))
        rated.sort(key=lambda i: (-(i.percent or 0), i.full_name, i.employee_key))
        items = rated + rest  # rest уже в порядке предварительного рейтинга
        mode = "llm" if rated else "fallback"
        running = any(b.status == PENDING for b in batches)
        can_next = (
            active_model is not None and not running and any(
                not self._search_fresh(by_key[p.key], active_model, results) for p in visible))
        return SearchState(
            query=q, status=status, mode=mode,
            notice=_search_notice(status, mode, errors),
            requested_by=run.requested_by if run else None, model=model if run else None,
            started_at=run.created_at if run else None,
            calculated_at=max((i.calculated_at for i in rated if i.calculated_at), default=None),
            stale=any(i.stale for i in rated), candidates_total=len(cands),
            evaluated=len(rated), total=len(items), items=items[offset:offset + limit],
            tasks=tasks, batches_total=len(batches), batches_done=n_done,
            batches_failed=n_failed, errors=errors,
            stage=_STAGE_NAME.get(batches[-1].stage) if batches else None,
            run_id=batches[-1].run_id or None if batches else None,
            deep_size=self._settings.deep_size(), remaining=len(rest),
            screened=len(screened), screen_total=sum(1 for c in cands if c.skills),
            can_evaluate_next=can_next)

    def _overview(
        self, batches, *, has_run: bool, chain_pending: bool = False,
    ) -> tuple[list[TaskInfo], str, int, int]:
        """Задачи пакетов и итоговый статус прогона (общее для сопоставления и поиска).
        ``chain_pending``: скрининг завершён, ступень 2 ещё не поставлена - прогон не завершён."""
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
            tasks.append(TaskInfo(b.task_id, tstatus, confirm, b.status, len(b.employee_keys),
                                  _STAGE_NAME.get(b.stage, DEEP), b.run_id))
        n_done = sum(b.status == DONE for b in batches)
        n_failed = sum(b.status == FAILED for b in batches)
        if not has_run:
            status = NONE
        elif awaiting and not running:
            status = AWAITING
        elif awaiting or running:
            status = RUNNING
        elif chain_pending and n_done:
            status = RUNNING
        elif n_failed and not n_done:
            status = FAILED_RUN
        elif n_failed:
            status = PARTIAL
        else:
            status = DONE_RUN
        return tasks, status, n_done, n_failed

    # ------------------------------------------------------------------ прогон целиком (FR-023)

    def _run_batches(self, run_id: str) -> tuple[str, list]:
        try:
            uuid.UUID(run_id)
        except ValueError:
            raise MatchingError("run_not_found", "Прогон не найден") from None
        batches = self._repo.batches_by_run(run_id)
        if batches:
            return "match", batches
        batches = self._repo.search_batches_by_run(run_id)
        if batches:
            return "search", batches
        raise MatchingError("run_not_found", "Прогон не найден")

    def run_info(self, login: str, run_id: str) -> RunInfo:
        """Прогон (одна ступень): статус, обезличенный предпросмотр всех пакетов, число подстановок.
        Только владелец: задачи шлюза принадлежат поставившему их пользователю."""
        kind, batches = self._run_batches(run_id)
        for b in batches:  # актуальные статусы: завершённые задачи -> записи кэша
            if b.status == PENDING:
                if kind == "match":
                    self._collect_batch(b)
                else:
                    self._collect_search_batch(b)
        batches = self._run_batches(run_id)[1]
        tasks: list[RunTask] = []
        replacements: dict[str, int] = {}
        external, provider = False, ""
        for b in batches:
            try:
                view = self._gw.get(login, b.task_id)
            except GatewayError:
                raise MatchingError("run_not_found", "Прогон не найден") from None
            external = external or view.is_external
            provider = provider or view.provider_name
            pv = view.preview
            for k, v in (pv.replacements if pv else {}).items():
                replacements[k] = replacements.get(k, 0) + v
            tasks.append(RunTask(
                b.task_id, view.status if b.status == PENDING else b.status,
                len(b.employee_keys), view.requires_confirmation and b.status == PENDING,
                pv.system if pv else None, pv.prompt if pv else None,
                dict(pv.replacements) if pv else {}, bool(pv and pv.ner_used),
                b.error_message,
                editable=bool(view.editable and b.status == PENDING),
                edited=bool(pv and pv.edited)))
        _, status, _, _ = self._overview(batches, has_run=True)
        need_key = batches[0].need_key if kind == "match" else None
        return RunInfo(
            run_id=run_id, kind=kind, stage=_STAGE_NAME.get(batches[0].stage, DEEP),
            status=status, requires_confirmation=any(t.requires_confirmation for t in tasks),
            is_external=external, provider_name=provider,
            candidates=sum(t.candidates for t in tasks), tasks=tasks, replacements=replacements,
            need_key=need_key)

    def confirm_run(self, login: str, run_id: str) -> RunInfo:
        """Подтвердить отправку всего прогона разом (FR-023): все задачи, ожидающие
        подтверждения, переходят в очередь одной транзакцией - либо ни одна."""
        info = self.run_info(login, run_id)
        ids = [t.task_id for t in info.tasks if t.requires_confirmation]
        if not ids:
            raise GatewayError(
                "not_awaiting_confirmation", "Прогон не ожидает подтверждения отправки")
        self._gw.confirm_many(login, ids)
        log.info("matching: прогон подтверждён", extra={
            "run_id": run_id, "tasks": len(ids), "stage": info.stage})
        return self.run_info(login, run_id)

    def edit_task_prompt(self, login: str, run_id: str, task_id: str, prompt: str) -> RunInfo:
        """Правка обезличенного текста одной задачи прогона до подтверждения (FR-023).
        Прогон и задача должны принадлежать пользователю; проверка остатков - в шлюзе."""
        info = self.run_info(login, run_id)
        task = next((t for t in info.tasks if t.task_id == task_id), None)
        if task is None:
            raise MatchingError("task_not_found", "Задача не найдена в прогоне")
        if not task.requires_confirmation:
            raise GatewayError(
                "not_editable", "Текст можно править только до подтверждения отправки")
        self._gw.edit_prompt(login, task_id, prompt)
        return self.run_info(login, run_id)

    def cancel_run(self, login: str, run_id: str) -> RunInfo:
        """Отменить неподтверждённые и ожидающие задачи прогона; результаты собранных пакетов
        сохраняются в кэше."""
        info = self.run_info(login, run_id)
        for t in info.tasks:
            if t.status in ("awaiting_confirmation", "queued", "running"):
                try:
                    self._gw.cancel(login, t.task_id)
                except GatewayError:
                    pass  # успела завершиться
        return self.run_info(login, run_id)

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
        """Ленивый сбор: завершённые задачи пакетов превращаются в записи кэша; завершённый
        скрининг запускает ступень 2."""
        for b in self._repo.list_batches(need_key):
            if b.status == PENDING:
                self._collect_batch(b)
        self._advance_match(need_key)

    def _active_model(self) -> str | None:
        try:
            return self._gw.resolve_provider(None).model or None
        except Exception:  # noqa: BLE001 - нет провайдера: сравнение моделей пропускаем
            return None
