"""Подмены для тестов шлюза LLM: репозиторий в памяти с теми же итогами, что у LlmRepo,
и клиент провайдера. Реальных ключей и сети нет: ключи тестовые, генерируются на запуск."""
from __future__ import annotations

import base64
import os
import time
import uuid
from datetime import UTC, datetime

from app.config import Secret, Settings
from app.llm_gateway import (
    Dictionaries,
    DictionarySource,
    LlmGateway,
    ProviderService,
)
from app.llm_gateway.anonymize import NerSpan
from app.llm_gateway.client import LlmCallError, LlmTimeoutError
from app.llm_gateway.repo import (
    AWAITING,
    CANCELLED,
    CONFLICT,
    DONE,
    FAILED,
    IN_USE,
    IS_DEFAULT,
    NOT_FOUND,
    OK,
    QUEUED,
    RUNNING,
    ProviderRow,
    TaskRow,
)
from app.secrets import SealedSecret, SecretStore

KEY32 = os.urandom(32)
ENC_KEY_B64 = base64.b64encode(KEY32).decode()
FAKE_PROVIDER_KEY = "test-llm-key-0000000000"  # заведомо фиктивный
NOW = datetime(2026, 10, 12, tzinfo=UTC)


class MemSecretRepo:
    """Подмена AppRepo для SecretStore: хранит ровно то, что ушло бы в secret_store."""

    def __init__(self) -> None:
        self.llm: dict[int, SealedSecret] = {}

    def upsert_llm_key_secret(self, pid, sealed):
        self.llm[pid] = sealed

    def get_llm_key_secret(self, pid):
        return self.llm.get(pid)

    def delete_llm_key_secret(self, pid):
        return self.llm.pop(pid, None) is not None


class FakeLlmRepo:
    def __init__(self, secrets: MemSecretRepo) -> None:
        self.providers: dict[int, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.settings: dict[str, str] = {}
        self.sources = {"companies": [], "persons": []}
        self._secrets = secrets
        self._next = 1

    # --- провайдеры ---
    def _row(self, pid) -> ProviderRow:
        p = self.providers[pid]
        return ProviderRow(pid, p["name"], p["url"], p["is_external"], p["is_default"],
                           p["timeout_sec"], p["model"], pid in self._secrets.llm, NOW)

    def list_providers(self):
        return sorted((self._row(i) for i in self.providers),
                      key=lambda r: (not r.is_default, r.name))

    def get_provider(self, pid):
        return self._row(pid) if pid in self.providers else None

    def get_default_provider(self):
        return next((self._row(i) for i, p in self.providers.items() if p["is_default"]), None)

    def count_providers(self):
        return len(self.providers)

    def create_provider(self, name, url, *, is_external, is_default, timeout_sec, model):
        if any(p["name"] == name for p in self.providers.values()):
            return None
        if is_default:
            for p in self.providers.values():
                p["is_default"] = False
        pid = self._next
        self._next += 1
        self.providers[pid] = dict(name=name, url=url, is_external=is_external,
                                   is_default=is_default, timeout_sec=timeout_sec, model=model)
        return pid

    def update_provider(self, pid, fields):
        if pid not in self.providers:
            return NOT_FOUND
        if "name" in fields and any(
                p["name"] == fields["name"] for i, p in self.providers.items() if i != pid):
            return CONFLICT
        if fields.get("is_default"):
            for p in self.providers.values():
                p["is_default"] = False
        self.providers[pid].update(fields)
        return OK

    def delete_provider(self, pid):
        if pid not in self.providers:
            return NOT_FOUND
        if self.providers[pid]["is_default"]:
            return IS_DEFAULT
        if any(t["provider_id"] == pid for t in self.tasks.values()):
            return IN_USE
        del self.providers[pid]
        self._secrets.llm.pop(pid, None)
        return OK

    # --- словари/настройки ---
    def dictionary_sources(self):
        return {k: list(v) for k, v in self.sources.items()}

    def get_setting(self, key):
        return self.settings.get(key)

    def put_setting(self, key, value):
        self.settings[key] = value

    # --- задачи ---
    def _task(self, t) -> TaskRow:
        return TaskRow(
            id=t["id"], owner_login=t["owner_login"], purpose=t["purpose"], ref=t["ref"],
            provider_id=t["provider_id"], is_external=t["is_external"], status=t["status"],
            payload=t["payload"], result=t["result"], error_code=t["error_code"],
            error_message=t["error_message"], attempts=t["attempts"], created_at=NOW,
            updated_at=NOW, confirmed_at=t["confirmed_at"], started_at=t["started_at"],
            finished_at=t["finished_at"],
        )

    def create_task(self, *, task_id, owner_login, purpose, ref, provider_id, is_external,
                    status, payload):
        self.tasks[task_id] = dict(
            id=task_id, owner_login=owner_login, purpose=purpose, ref=ref,
            provider_id=provider_id, is_external=is_external, status=status, payload=payload,
            result=None, error_code=None, error_message=None, attempts=0, confirmed_at=None,
            started_at=None, finished_at=None, order=len(self.tasks),
        )
        return self._task(self.tasks[task_id])

    def get_task(self, task_id):
        t = self.tasks.get(task_id)
        return self._task(t) if t else None

    def list_tasks(self, owner_login, limit=50):
        rows = [t for t in self.tasks.values() if t["owner_login"] == owner_login]
        return [self._task(t) for t in sorted(rows, key=lambda t: -t["order"])][:limit]

    def confirm_task(self, task_id):
        t = self.tasks[task_id]
        if t["status"] != AWAITING:
            return False
        t.update(status=QUEUED, confirmed_at=NOW)
        return True

    def confirm_tasks(self, task_ids):
        ids = set(task_ids)
        if not ids or any(self.tasks[i]["status"] != AWAITING for i in ids):
            return False
        for i in ids:
            self.tasks[i].update(status=QUEUED, confirmed_at=NOW)
        return True

    def cancel_task(self, task_id):
        t = self.tasks[task_id]
        if t["status"] not in (AWAITING, QUEUED, RUNNING):
            return False
        t.update(status=CANCELLED, payload=None, finished_at=NOW)
        return True

    def requeue_task(self, task_id):
        t = self.tasks[task_id]
        if t["status"] not in (FAILED, "timeout") or t["payload"] is None:
            return False
        t.update(status=QUEUED, error_code=None, error_message=None, finished_at=None)
        return True

    def claim_next(self):
        queued = sorted((t for t in self.tasks.values() if t["status"] == QUEUED),
                        key=lambda t: t["order"])
        if not queued:
            return None
        t = queued[0]
        t.update(status=RUNNING, started_at=NOW, attempts=t["attempts"] + 1)
        return self._task(t)

    def finish_ok(self, task_id, result):
        t = self.tasks[task_id]
        if t["status"] != RUNNING:
            return False
        t.update(status=DONE, result=result, payload=None, finished_at=NOW)
        return True

    def finish_error(self, task_id, status, code, message):
        t = self.tasks[task_id]
        if t["status"] != RUNNING:
            return False
        t.update(status=status, error_code=code, error_message=message, finished_at=NOW)
        return True

    def fail_stale_running(self, grace_sec=30):
        return 0

    def purge_finished(self, days):
        return 0


class FakeClient:
    """Клиент провайдера: запоминает вызовы; ответ - функция от prompt."""

    def __init__(self, reply=None, *, error: Exception | None = None, delay: float = 0.0):
        self.calls: list[dict] = []
        self._reply = reply or (lambda prompt: "ответ: " + prompt)
        self._error = error
        self._delay = delay

    def complete(self, *, url, api_key, model, system, prompt, timeout_sec):
        self.calls.append(dict(url=url, api_key=api_key, model=model, system=system,
                               prompt=prompt, timeout_sec=timeout_sec))
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._reply(prompt)


class FakeNer:
    """NER по списку (подстрока, вид): детерминированная замена natasha в тестах."""

    def __init__(self, items: list[tuple[str, str]]) -> None:
        self.items = items

    def extract(self, text):
        out = []
        for needle, kind in self.items:
            start = text.find(needle)
            while start != -1:
                out.append(NerSpan(kind, start, start + len(needle)))
                start = text.find(needle, start + len(needle))
        return out


class Env:
    """Шлюз со всеми подменами. Провайдеры: внутренняя Qwen (по умолчанию) и внешний."""

    def __init__(self, client=None, ner=None, env_key: str = "", external_key=FAKE_PROVIDER_KEY,
                 external_timeout: int = 120):
        self.secrets = MemSecretRepo()
        self.repo = FakeLlmRepo(self.secrets)
        self.store = SecretStore(self.secrets, KEY32, "k1")
        self.client = client or FakeClient()
        self.dictionary = DictionarySource(self.repo)
        self.gateway = LlmGateway(self.repo, self.store, self.dictionary, self.client,
                                  ner=ner, env_key=Secret(env_key) if env_key else None)
        self.providers = ProviderService(self.repo, self.store)
        self.internal = self.providers.create(
            "Qwen (внутренняя)", "http://qwen.test/api/v1", is_external=False, api_key=None,
            model="qwen-test", is_default=True)
        self.external = self.providers.create(
            "Внешняя модель", "https://llm.example.test/v1", is_external=True,
            api_key=external_key, model="ext-model", timeout_sec=external_timeout)

    def settings(self) -> Settings:
        return Settings(app_db_url=Secret("postgresql://x"), enc_key=Secret(ENC_KEY_B64))


def new_id() -> str:
    return str(uuid.uuid4())


__all__ = ["Dictionaries", "LlmCallError", "LlmTimeoutError"]
