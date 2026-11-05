"""Логика тезисов правил клиента (KON-160, REW-28): кэш по хешу комментариев, ленивый пересчёт.

Комментарии эпика уже в кэше (worker, app.epic_comment). Тезисы пересчитываются LLM только
когда изменился хеш комментариев (типично 1 раз в 1-2 мес на клиента). Задача шлюза
``purpose="epic_rules"`` ставится от системного владельца; для внешнего провайдера
обезличивание делает шлюз (FR-023), но такая задача ждёт подтверждения - «Обновить» вручную
может только админ (POST regenerate от своего логина: подтверждение отправки - его).

Источники тезиса - id комментариев Jira; ответ отдаётся со ссылкой и автором каждого.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..llm_gateway.repo import FINAL, TaskRow
from .prompts import EPIC_RULES_SYSTEM, build_prompt, parse_result

log = logging.getLogger(__name__)

PURPOSE = "epic_rules"
SYSTEM_OWNER = "system"  # задачи пересчёта по расписанию/открытию; peek читает без владельца

ST_OK = "ok"
ST_REGENERATING = "regenerating"
ST_EMPTY = "empty"


class ClientRulesError(Exception):
    """Отказ операции (код + сообщение безопасно показывать пользователю)."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"[{code}] {message}")


class SyncData(Protocol):
    """Часть ``SyncRepo``: кэш комментариев и эпиков (app_db)."""

    def epic_key_for_need(self, need_key: str) -> str | None: ...
    def need_exists(self, need_key: str) -> bool: ...
    def get_epic(self, epic_key: str): ...
    def epic_comments(self, epic_key: str) -> list[dict[str, str | None]]: ...
    def epic_comments_hash(self, epic_key: str) -> str | None: ...
    def epic_theses(self, epic_key: str) -> dict | None: ...
    def save_epic_theses(
        self, epic_key: str, comments_hash: str, theses: list[dict], provider_name: str,
    ) -> None: ...


class Gateway(Protocol):
    """Часть ``LlmGateway``, которую использует модуль."""

    def submit(self, owner_login: str, purpose: str, prompt: str, *, system: str | None = None,
               provider_id: int | None = None, ref: str | None = None, terms=None): ...
    def peek(self, task_id: str): ...


class LlmTasks(Protocol):
    """Часть ``LlmRepo``: поиск «висящей» задачи (KON-160) и отмена при повторе."""

    def latest_task(self, purpose: str, ref: str) -> TaskRow | None: ...
    def cancel_task(self, task_id: str) -> bool: ...


class ClientRulesService:
    def __init__(self, repo: SyncData, llm_repo: LlmTasks, gateway: Gateway,
                 *, jira_base_url: str = "") -> None:
        self._repo = repo
        self._llm_repo = llm_repo
        self._gw = gateway
        self._jira_base = jira_base_url.rstrip("/")

    # --- чтение (GET theses) ---

    def get(self, need_key: str) -> dict[str, Any]:
        """Тезисы для раскрытой строки потребности. Кэш по хешу: устарел - «Обновляется…»
        (202): задача пересчёта ставится здесь лениво (системный владелец; внешний провайдер
        не подтверждается системно - тогда ждёт ручного «Обновить» админом)."""
        self._need(need_key)
        epic_key = self._repo.epic_key_for_need(need_key)
        if not epic_key:
            raise ClientRulesError(
                "need_no_client", "У потребности не указан клиент (эпик)", status=409)
        current = self._repo.epic_comments_hash(epic_key)
        if current is None:
            return {
                "status": ST_EMPTY, "epic_key": epic_key,
                "theses": [], "generated_at": None, "provider": None,
                "need_key": need_key,
            }
        cached = self._repo.epic_theses(epic_key)
        if cached is not None and cached["comments_hash"] == current:
            return {
                "status": ST_OK, "epic_key": epic_key, "need_key": need_key,
                "theses": self._with_sources(epic_key, visited=cached["theses"]),
                "generated_at": cached["generated_at"], "provider": cached["provider_name"],
            }
        submitted = self._ensure_task(epic_key, current)
        stale = cached["theses"] if cached is not None else []
        return {
            "status": ST_REGENERATING, "epic_key": epic_key, "need_key": need_key,
            "theses": self._with_sources(epic_key, visited=stale),
            "generated_at": cached["generated_at"] if cached else None,
            "provider": cached["provider_name"] if cached else None,
            "task_submitted": submitted,
        }

    def _need(self, need_key: str) -> None:
        if not self._repo.need_exists(need_key):
            raise ClientRulesError("need_not_found", "Потребность не найдена", status=404)

    def _with_sources(self, epic_key: str, *, visited: list[dict]) -> list[dict]:
        """Источники тезисов дополнить автором/датой/ссылкой; неизвестные id отсеиваются."""
        comments = {}
        for c in self._repo.epic_comments(epic_key):
            comments[str(c.get("id") or "")] = c
        out = []
        for t in visited:
            sources = []
            for s in t.get("sources") or []:
                c = comments.get(str(s.get("id") or ""))
                if c is None:
                    continue
                url = (f"{self._jira_base}/browse/{epic_key}"
                       f"?commentId={c['id']}#comment-{c['id']}") if self._jira_base else None
                sources.append({
                    "id": c.get("id"), "author": c.get("author"), "date": c.get("updated"),
                    "url": url,
                })
            out.append({
                "text": t.get("text"), "contradicts_rules": bool(t.get("contradicts_rules")),
                "sources": sources,
            })
        return out

    # --- постановка и ручной пересчёт ---

    def _ensure_task(self, epic_key: str, comments_hash: str) -> str | None:
        """Задача пересчёта: пропускаем, если такая уже висит/стоит. ref хранит хеш
        комментариев, от которого считается результат: "epic_key:hash"."""
        ref = _ref(epic_key, comments_hash)
        row = self._llm_repo.latest_task(PURPOSE, ref)
        if row is not None and row.status not in FINAL:
            return None  # уже считается (идемпотентность повторных открытий блока)
        if row is not None and row.status == "done":
            # результат на этот ref уже был: сначала попробуем подобрать его в кэш заново
            self.collect_task(row.id)
            cached = self._repo.epic_theses(epic_key)
            if cached is not None and cached["comments_hash"] == comments_hash:
                return None
            # сбор не удался (LLM вернула мусор): повторяем, но не чаще раза в 5 минут
            now = datetime.now(UTC)
            if row.finished_at and now - row.finished_at < timedelta(minutes=5):
                log.warning("тезисы этого ref не подбираются: повтор позже",
                            extra={"epic_key": epic_key, "task_id": row.id})
                return None
        task = self._gw.submit(
            SYSTEM_OWNER, PURPOSE, self._prompt(epic_key), system=EPIC_RULES_SYSTEM,
            provider_id=None, ref=ref, terms=None,
        )
        if task.requires_confirmation:
            log.info("тезисы ждут подтверждения: внешний провайдер, системно не отправляем",
                     extra={"epic_key": epic_key, "task_id": task.id})
        else:
            log.info("задача пересчёта тезисов поставлена",
                     extra={"epic_key": epic_key, "task_id": task.id})
        return task.id

    def regenerate(self, need_key: str, owner_login: str, provider_id: int | None) -> dict:
        """Ручное «Обновить» (только админ): задача от логина админа; для внешнего провайдера
        подтвердить отправку - он сам (в «Задачах LLM», FR-023). Висящую задачу того же ref
        отменяем поверх - считаем состояние на момент вызова."""
        self._need(need_key)
        epic_key = self._repo.epic_key_for_need(need_key)
        if not epic_key:
            raise ClientRulesError(
                "need_no_client", "У потребности не указан клиент (эпик)", status=409)
        current = self._repo.epic_comments_hash(epic_key)
        if current is None:
            raise ClientRulesError("no_comments", "У эпика нет комментариев", status=409)
        ref = _ref(epic_key, current)
        row = self._llm_repo.latest_task(PURPOSE, ref)
        if row is not None and row.status not in FINAL:
            self._llm_repo.cancel_task(row.id)
        task = self._gw.submit(
            owner_login, PURPOSE, self._prompt(epic_key), system=EPIC_RULES_SYSTEM,
            provider_id=provider_id, ref=ref, terms=None,
        )
        return {
            "task_id": task.id, "status": task.status,
            "requires_confirmation": task.requires_confirmation,
            "provider": task.provider_name,
        }

    def _prompt(self, epic_key: str) -> str:
        epic = self._repo.get_epic(epic_key)
        epic_name = epic.name if epic is not None else epic_key
        rules = epic.rules if epic is not None else []
        comments = self._repo.epic_comments(epic_key)
        return build_prompt(epic_name, rules, comments)

    # --- сбор результата (worker) ---

    def collect_task(self, task_id: str) -> None:
        """Обработчик on_finished шлюза: результат эпик-правил уходит в кэш тезисов."""
        view = self._gw.peek(task_id)
        if view is None or view.purpose != PURPOSE or view.status != "done":
            return
        ref = view.ref or ""
        if ":" not in ref:
            log.warning("задача epic_rules без ref-хеша: результат не сохранён",
                        extra={"task_id": task_id})
            return
        epic_key, _, comments_hash = ref.rpartition(":")
        if not epic_key:
            return
        theses = parse_result(view.result or "")
        # источники сверяем с кэшем комментариев: несуществующие id от LLM отсеиваем
        known = {str(c.get("id") or "") for c in self._repo.epic_comments(epic_key)}
        cleaned = []
        for t in theses:
            src = [s for s in t.get("sources") or [] if s.get("id") in known]
            cleaned.append({**t, "sources": [{"id": s["id"]} for s in src]})
        if not cleaned:
            log.warning("LLM вернула пустые тезисы: кэш не обновлён",
                        extra={"task_id": task_id, "epic_key": epic_key})
            return
        self._repo.save_epic_theses(epic_key, comments_hash, cleaned, view.provider_name)
        log.info("тезисы правил клиента сохранены",
                 extra={"epic_key": epic_key, "theses": len(cleaned)})


def _ref(epic_key: str, comments_hash: str) -> str:
    return f"{epic_key}:{comments_hash[:16]}"


def build_client_rules(
    repo: SyncData, llm_repo: LlmTasks, gateway: Gateway, *, jira_base_url: str = "",
) -> ClientRulesService:
    return ClientRulesService(repo, llm_repo, gateway, jira_base_url=jira_base_url)
