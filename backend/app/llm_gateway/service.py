"""Шлюз LLM (ADR-007): провайдер -> обезличивание (внешний) -> предпросмотр -> подтверждение ->
вызов с таймаутом -> обратная подстановка -> результат.

Все обращения к LLM в приложении идут через ``LlmGateway``. Внутренняя Qwen: текст уходит как
есть (FR-023, критерий 3). Внешний провайдер: задача встаёт в ``awaiting_confirmation`` и в
очередь worker попадает только после ``confirm`` пользователя (критерий 2). Подстановки,
исходящий текст и результат хранятся шифртекстом в ``llm_task`` и очищаются по завершении.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime

from ..config import Secret
from ..secrets import SecretsError, SecretStore
from .anonymize import (
    LABELS,
    AnonymizationError,
    Anonymizer,
    Dictionaries,
    NerPort,
    restore,
)
from .client import ChatClient, LlmCallError, LlmTimeoutError
from .dictionaries import DictionarySource
from .repo import (
    AWAITING,
    CANCELLED,
    FAILED,
    QUEUED,
    TIMEOUT,
    LlmRepo,
    ProviderRow,
    TaskRow,
)

log = logging.getLogger(__name__)

DEFAULT_QWEN_URL = "http://ollama-gpu.simbirsoft.com:3000/api/v1"
DEFAULT_PROVIDER_NAME = "Qwen (внутренняя)"
DEFAULT_TIMEOUT_SEC = 120  # NFR-010
MAX_PROMPT_CHARS = 200_000
_SEP = "\x1e"  # разделитель system/prompt при общей нумерации плейсхолдеров


class GatewayError(Exception):
    """Ошибка шлюза с машинным кодом; сообщение безопасно показывать пользователю."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Preview:
    """Что уйдёт во внешнюю LLM (обезличенные тексты) и сводка подстановок."""

    system: str | None
    prompt: str
    replacements: dict[str, int]
    ner_used: bool
    edited: bool = False  # пользователь правил текст предпросмотра (FR-023)


class LeakError(GatewayError):
    """В отредактированном тексте найдены необезличенные фрагменты; текст не сохранён.

    ``fragments`` - то, что нашла проверка (пользователь сам это написал, ему и показываем);
    в логи фрагменты не пишутся."""

    def __init__(self, fragments: list[str]) -> None:
        super().__init__(
            "leak_detected",
            "В тексте остались необезличенные данные: удалите или замените их плейсхолдерами")
        self.fragments = fragments


@dataclass(frozen=True)
class TaskView:
    id: str
    purpose: str
    ref: str | None
    status: str
    provider_id: int
    provider_name: str
    is_external: bool
    requires_confirmation: bool
    preview: Preview | None
    result: str | None
    error_code: str | None
    error_message: str | None
    timeout_sec: int
    attempts: int
    created_at: datetime
    finished_at: datetime | None
    editable: bool = False  # внешняя задача до подтверждения: текст можно править


_NOT_EDITABLE = "Текст можно править только до подтверждения отправки во внешнюю LLM"


def _aad(task_id: str, part: str) -> str:
    return f"llm_task:{task_id}:{part}"


class LlmGateway:
    def __init__(
        self,
        repo: LlmRepo,
        store: SecretStore,
        dictionaries: DictionarySource,
        client: ChatClient,
        ner: NerPort | None = None,
        env_key: Secret | None = None,
    ) -> None:
        self._repo = repo
        self._store = store
        self._dicts = dictionaries
        self._client = client
        self._ner = ner
        self._env_key = env_key

    # --- постановка и подтверждение ---

    def submit(
        self, owner_login: str, purpose: str, prompt: str, *, system: str | None = None,
        provider_id: int | None = None, ref: str | None = None,
        terms: Dictionaries | None = None,
    ) -> TaskView:
        """Поставить задачу. Для внешнего провайдера текст обезличивается сразу, задача ждёт
        подтверждения; для внутреннего - сразу в очередь."""
        prompt = prompt.replace(_SEP, " ")
        if not prompt.strip():
            raise GatewayError("empty_prompt", "Пустой запрос к LLM")
        if len(prompt) + len(system or "") > MAX_PROMPT_CHARS:
            raise GatewayError("prompt_too_long", "Текст запроса слишком длинный")
        provider = self._pick_provider(provider_id)
        system = system.replace(_SEP, " ") if system else None

        payload: dict = {"system": system, "prompt": prompt, "mapping": {}, "counts": {},
                         "ner": False}
        if provider.is_external:
            payload = self._anonymize(system, prompt, terms)
            status = AWAITING
        else:
            status = QUEUED

        task_id = str(uuid.uuid4())
        sealed = self._store.seal_blob(json.dumps(payload, ensure_ascii=False),
                                       _aad(task_id, "payload"))
        row = self._repo.create_task(
            task_id=task_id, owner_login=owner_login, purpose=purpose, ref=ref,
            provider_id=provider.id, is_external=provider.is_external, status=status,
            payload=sealed,
        )
        log.info("задача LLM создана", extra={
            "task_id": task_id, "purpose": purpose, "provider_id": provider.id,
            "external": provider.is_external, "status": status,
        })
        return self._view(row, provider)

    def _anonymize(self, system: str | None, prompt: str, terms: Dictionaries | None) -> dict:
        d = self._dicts.load()
        if terms is not None:
            d = d.merged(terms)
        anonymizer = Anonymizer(d, self._ner, self._dicts.load_tech_terms())
        joined = (system or "") + _SEP + prompt
        anon = anonymizer.anonymize(joined)
        out_system, out_prompt = anon.text.split(_SEP, 1)
        leaks = anonymizer.find_leaks(out_system + "\n" + out_prompt)
        if leaks:
            # сами строки в сообщение не попадают (они и есть ПДн)
            log.error("обезличивание: остались опознаваемые ПДн", extra={"leaks": len(leaks)})
            raise AnonymizationError(f"осталось {len(leaks)} фрагментов")
        return {"system": out_system or None, "prompt": out_prompt, "mapping": anon.mapping,
                "counts": anon.counts, "ner": anon.ner_used, "orig_len": len(out_prompt),
                "terms": _terms_json(terms)}

    def _pick_provider(self, provider_id: int | None) -> ProviderRow:
        provider = (self._repo.get_provider(provider_id) if provider_id is not None
                    else self._repo.get_default_provider())
        if provider is None:
            if provider_id is not None:
                raise GatewayError("provider_not_found", "Провайдер LLM не найден")
            raise GatewayError("no_default_provider", "Провайдер LLM по умолчанию не настроен")
        return provider

    def confirm(self, owner_login: str, task_id: str) -> TaskView:
        row = self._own(owner_login, task_id)
        if not self._repo.confirm_task(row.id):
            raise GatewayError(
                "not_awaiting_confirmation", "Задача не ожидает подтверждения отправки")
        return self.get(owner_login, task_id)

    def confirm_many(self, owner_login: str, task_ids: list[str]) -> list[TaskView]:
        """Групповое подтверждение прогона (FR-023): все задачи владельца разом или ни одной.
        Подтверждается тот предпросмотр, который пользователь видел (текст задач неизменен)."""
        rows = [self._own(owner_login, t) for t in task_ids]
        if not rows or not self._repo.confirm_tasks([r.id for r in rows]):
            raise GatewayError(
                "not_awaiting_confirmation", "Прогон не ожидает подтверждения отправки")
        return [self.get(owner_login, r.id) for r in rows]

    def edit_prompt(self, owner_login: str, task_id: str, new_prompt: str) -> TaskView:
        """Правка обезличенного текста внешнего запроса до подтверждения (CR владельца, FR-023).

        Перед сохранением текст проходит ту же проверку остатков, что и при постановке
        (regex, словари, NER), плюс оригиналы подстановок этой задачи. Найдены фрагменты -
        ``LeakError``, текст не сохраняется. Плейсхолдеры вне таблицы подстановок допустимы."""
        row = self._own(owner_login, task_id)
        if not row.is_external or row.status != AWAITING or row.confirmed_at is not None \
                or row.payload is None:
            raise GatewayError("not_editable", _NOT_EDITABLE)
        try:
            payload = json.loads(self._store.open_blob(row.payload, _aad(row.id, "payload")))
        except (SecretsError, ValueError):
            raise GatewayError("payload_unreadable", "Не удалось прочитать запрос") from None
        new_prompt = new_prompt.replace(_SEP, " ")
        if not new_prompt.strip():
            raise GatewayError("empty_prompt", "Пустой запрос к LLM")
        orig_len = int(payload.get("orig_len") or len(payload["prompt"]))
        limit = min(orig_len * 2, MAX_PROMPT_CHARS - len(payload.get("system") or ""))
        if len(new_prompt) > limit:
            raise GatewayError(
                "bad_prompt_length",
                f"Текст длиннее допустимого: не более {limit} символов (2 длины исходного)")
        leaks = self._find_edit_leaks(payload, new_prompt)
        if leaks:
            log.warning("правка текста отклонена: остались ПДн", extra={
                "task_id": row.id, "leaks": len(leaks)})
            raise LeakError(leaks)
        payload.update(prompt=new_prompt, edited=True, orig_len=orig_len)
        sealed = self._store.seal_blob(json.dumps(payload, ensure_ascii=False),
                                       _aad(row.id, "payload"))
        if not self._repo.update_payload(row.id, sealed):
            raise GatewayError("not_editable", _NOT_EDITABLE)  # успели подтвердить/отменить
        log.info("текст внешнего запроса изменён пользователем", extra={
            "task_id": row.id, "chars": len(new_prompt)})
        return self.get(owner_login, task_id)

    def _find_edit_leaks(self, payload: dict, text: str) -> list[str]:
        """Словари + оригиналы подстановок задачи (regex/словари) и NER по новым фрагментам."""
        d = self._dicts.load()
        saved = payload.get("terms")
        if saved:
            d = d.merged(Dictionaries(**{k: tuple(v) for k, v in saved.items()}))
        d = d.merged(_originals_as_terms(payload.get("mapping") or {}))
        anonymizer = Anonymizer(d, None, self._dicts.load_tech_terms())
        found = anonymizer.find_leaks(text)
        if self._ner is not None:
            clean = _blank_placeholders(text)
            before = (payload.get("prompt") or "").lower()
            try:
                spans = self._ner.extract(clean)
            except Exception as exc:  # noqa: BLE001 - без проверки NER сохранять нельзя
                log.error("правка текста: сбой NER", extra={"error": type(exc).__name__})
                raise AnonymizationError("NER недоступен") from None
            for sp in spans:
                frag = " ".join(clean[sp.start:sp.end].split())
                # то, что уже стояло в принятом тексте, не считается новой утечкой
                if (len(frag) >= 3 and frag.lower() not in before
                        and not anonymizer.is_known_tech(frag)):
                    found.append(frag)
        seen: dict[str, str] = {}
        for f in found:
            seen.setdefault(f.lower(), f)
        return list(seen.values())

    def cancel(self, owner_login: str, task_id: str) -> TaskView:
        row = self._own(owner_login, task_id)
        if not self._repo.cancel_task(row.id):
            raise GatewayError("not_cancellable", "Задача уже завершена")
        return self.get(owner_login, task_id)

    def retry(self, owner_login: str, task_id: str) -> TaskView:
        """Повтор после таймаута или ошибки (NFR-010). Подтверждённый текст не меняется."""
        row = self._own(owner_login, task_id)
        if not self._repo.requeue_task(row.id):
            raise GatewayError("not_retryable", "Повторить можно только после ошибки или таймаута")
        return self.get(owner_login, task_id)

    def get(self, owner_login: str, task_id: str) -> TaskView:
        row = self._own(owner_login, task_id)
        return self._view(row, self._repo.get_provider(row.provider_id))

    def resolve_provider(self, provider_id: int | None = None) -> ProviderRow:
        """Провайдер, который будет использован для задачи (по умолчанию - основной). Модулям
        приложения нужно имя модели заранее: для ключа кэша результата (FR-005)."""
        return self._pick_provider(provider_id)

    def peek(self, task_id: str) -> TaskView | None:
        """Состояние и результат задачи БЕЗ проверки владельца: только для модулей приложения,
        поставивших эту задачу (matching собирает результат за пользователя). Наружу не
        отдаётся: пользовательский API идёт через ``get`` с проверкой владельца."""
        try:
            uuid.UUID(task_id)
        except ValueError:
            return None
        row = self._repo.get_task(task_id)
        if row is None:
            return None
        return self._view(row, self._repo.get_provider(row.provider_id))

    def list_tasks(self, owner_login: str, limit: int = 50) -> list[TaskView]:
        providers = {p.id: p for p in self._repo.list_providers()}
        return [self._view(r, providers.get(r.provider_id))
                for r in self._repo.list_tasks(owner_login, limit)]

    def _own(self, owner_login: str, task_id: str) -> TaskRow:
        try:
            uuid.UUID(task_id)
        except ValueError:
            raise GatewayError("task_not_found", "Задача не найдена") from None
        row = self._repo.get_task(task_id)
        if row is None or row.owner_login != owner_login:
            raise GatewayError("task_not_found", "Задача не найдена")
        return row

    def _view(self, row: TaskRow, provider: ProviderRow | None) -> TaskView:
        preview = None
        if row.payload is not None:
            try:
                p = json.loads(self._store.open_blob(row.payload, _aad(row.id, "payload")))
                preview = Preview(system=p.get("system"), prompt=p["prompt"],
                                  replacements=p.get("counts", {}), ner_used=p.get("ner", False),
                                  edited=bool(p.get("edited", False)))
            except (SecretsError, ValueError, KeyError):
                preview = None
        result = None
        if row.result is not None:
            try:
                result = self._store.open_blob(row.result, _aad(row.id, "result"))
            except SecretsError:
                result = None
        return TaskView(
            id=row.id, purpose=row.purpose, ref=row.ref, status=row.status,
            provider_id=row.provider_id,
            provider_name=provider.name if provider else "",
            is_external=row.is_external,
            requires_confirmation=row.status == AWAITING,
            preview=preview if row.is_external else None,
            result=result, error_code=row.error_code, error_message=row.error_message,
            timeout_sec=provider.timeout_sec if provider else DEFAULT_TIMEOUT_SEC,
            attempts=row.attempts, created_at=row.created_at, finished_at=row.finished_at,
            editable=bool(row.is_external and row.status == AWAITING and preview is not None
                          and row.confirmed_at is None),
        )

    # --- выполнение (worker) ---

    def run_next(self) -> TaskRow | None:
        """Взять одну задачу из очереди и выполнить; None, если очередь пуста."""
        self._repo.fail_stale_running()
        task = self._repo.claim_next()
        if task is None:
            return None
        self._execute(task)
        return task

    def _execute(self, task: TaskRow) -> None:
        t0 = time.monotonic()
        try:
            self._execute_inner(task)
        except Exception as exc:  # noqa: BLE001 - сбой одной задачи не валит worker
            log.error("сбой задачи LLM", extra={
                "task_id": task.id, "error": type(exc).__name__})
            self._repo.finish_error(task.id, FAILED, "internal_error",
                                    "Внутренняя ошибка шлюза LLM. Повторите запрос.")
        log.info("задача LLM обработана", extra={
            "task_id": task.id, "elapsed_sec": round(time.monotonic() - t0, 2)})

    def _execute_inner(self, task: TaskRow) -> None:
        # отправка внешнему провайдеру без подтверждённого предпросмотра невозможна
        if task.is_external and task.confirmed_at is None:
            self._repo.finish_error(task.id, FAILED, "not_confirmed",
                                    "Отправка не подтверждена пользователем")
            return
        provider = self._repo.get_provider(task.provider_id)
        if provider is None or task.payload is None:
            self._repo.finish_error(task.id, FAILED, "provider_not_found",
                                    "Провайдер LLM не найден")
            return
        try:
            payload = json.loads(self._store.open_blob(task.payload, _aad(task.id, "payload")))
        except (SecretsError, ValueError):
            self._repo.finish_error(task.id, FAILED, "payload_unreadable",
                                    "Не удалось прочитать запрос (ключ шифрования изменился)")
            return
        if not provider.model:
            self._repo.finish_error(task.id, FAILED, "no_model",
                                    "У провайдера не задана модель (Настройки LLM)")
            return
        key = self._api_key(provider)
        if provider.is_external and not key:
            self._repo.finish_error(task.id, FAILED, "no_key",
                                    "У провайдера не задан API-ключ (Настройки LLM)")
            return
        try:
            content = self._call(provider, key, payload)
        except LlmTimeoutError as exc:
            self._repo.finish_error(task.id, TIMEOUT, exc.code, exc.message)
            return
        except LlmCallError as exc:
            self._repo.finish_error(task.id, FAILED, exc.code, exc.message)
            return
        text = restore(content, payload.get("mapping") or {}) if provider.is_external else content
        sealed = self._store.seal_blob(text, _aad(task.id, "result"))
        self._repo.finish_ok(task.id, sealed)

    def _api_key(self, provider: ProviderRow) -> str | None:
        stored = self._store.load_llm_key(provider.id)
        if stored:
            return stored
        if not provider.is_external and self._env_key:
            return self._env_key.reveal()  # LLM_API_KEY: ключ внутренней Qwen из env
        return None

    def _call(self, provider: ProviderRow, key: str | None, payload: dict) -> str:
        """Вызов с жёстким сроком: таймаут провайдера - предел и для зависшего ответа."""
        timeout = float(provider.timeout_sec)
        ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-call")
        fut = ex.submit(
            self._client.complete, url=provider.url, api_key=key, model=provider.model,
            system=payload.get("system"), prompt=payload["prompt"], timeout_sec=timeout,
        )
        try:
            return fut.result(timeout=timeout)
        except FutureTimeout:
            raise LlmTimeoutError(timeout) from None
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    def purge_old(self, days: int) -> int:
        return self._repo.purge_finished(days)


def _terms_json(terms: Dictionaries | None) -> dict | None:
    if terms is None or terms.is_empty():
        return None
    return {"persons": list(terms.persons), "companies": list(terms.companies),
            "projects": list(terms.projects), "locations": list(terms.locations)}


_PH_RE = re.compile(r"\[(?:%s)_\d+\]" % "|".join(LABELS.values()))


def _blank_placeholders(text: str) -> str:
    """Плейсхолдеры -> пробелы той же длины: смещения NER остаются верными."""
    return _PH_RE.sub(lambda m: " " * len(m.group(0)), text)


def _originals_as_terms(mapping: dict[str, str]) -> Dictionaries:
    """Оригиналы подстановок задачи как словари: по виду метки плейсхолдера."""
    buckets: dict[str, list[str]] = {
        "persons": [], "companies": [], "projects": [], "locations": []}
    kind = {LABELS["person"]: "persons", LABELS["company"]: "companies",
            LABELS["location"]: "locations"}
    for ph, original in mapping.items():
        label = ph.strip("[]").rsplit("_", 1)[0]
        buckets[kind.get(label, "projects")].append(original)
    return Dictionaries(**{k: tuple(v) for k, v in buckets.items()})


__all__ = [
    "CANCELLED", "DEFAULT_PROVIDER_NAME", "DEFAULT_QWEN_URL", "DEFAULT_TIMEOUT_SEC",
    "AnonymizationError", "GatewayError", "LeakError", "LlmGateway", "Preview", "TaskView",
]
