"""Настройки провайдеров LLM (FR-030): создание, изменение, ключи шифртекстом, провайдер по
умолчанию."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..config import Settings
from ..secrets import SecretStore
from .repo import CONFLICT, IN_USE, IS_DEFAULT, NOT_FOUND, OK, LlmRepo, ProviderRow
from .service import DEFAULT_PROVIDER_NAME, DEFAULT_QWEN_URL, DEFAULT_TIMEOUT_SEC

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"^https?://[^\s/]+(?:/\S*)?$")


class ProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_url(url: str, is_external: bool) -> str:
    url = url.strip().rstrip("/")
    if not _URL_RE.match(url):
        raise ProviderError("invalid_url", "URL должен начинаться с http:// или https://")
    if is_external and not url.lower().startswith("https://"):
        # исходящий трафик во внешнюю сеть - только HTTPS (05_deployment_security)
        raise ProviderError("insecure_url", "Для внешнего провайдера допустим только HTTPS")
    return url


def validate_timeout(value: int) -> int:
    if not 1 <= value <= 3600:
        raise ProviderError("invalid_timeout", "Таймаут: от 1 до 3600 секунд")
    return value


def validate_name(name: str) -> str:
    name = " ".join(name.split())
    if not 1 <= len(name) <= 128:
        raise ProviderError("invalid_name", "Название: от 1 до 128 символов")
    return name


@dataclass
class ProviderService:
    repo: LlmRepo
    store: SecretStore

    def create(
        self, name: str, url: str, *, is_external: bool, api_key: str | None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC, model: str = "", is_default: bool = False,
    ) -> ProviderRow:
        name = validate_name(name)
        url = validate_url(url, is_external)
        validate_timeout(timeout_sec)
        if is_external and not api_key:
            raise ProviderError("key_required", "Для внешнего провайдера нужен API-ключ")
        pid = self.repo.create_provider(
            name, url, is_external=is_external, is_default=is_default,
            timeout_sec=timeout_sec, model=model.strip(),
        )
        if pid is None:
            raise ProviderError("name_taken", "Провайдер с таким названием уже есть")
        if api_key:
            self.store.save_llm_key(pid, api_key)
        return self.repo.get_provider(pid)

    def update(
        self, provider_id: int, *, name: str | None = None, url: str | None = None,
        is_external: bool | None = None, timeout_sec: int | None = None,
        model: str | None = None, is_default: bool | None = None,
        api_key: str | None = None, remove_key: bool = False,
    ) -> ProviderRow:
        current = self.repo.get_provider(provider_id)
        if current is None:
            raise ProviderError("not_found", "Провайдер не найден")
        fields: dict = {}
        ext = current.is_external if is_external is None else is_external
        if name is not None:
            fields["name"] = validate_name(name)
        if url is not None or is_external is not None:
            fields["url"] = validate_url(url if url is not None else current.url, ext)
        if is_external is not None:
            fields["is_external"] = is_external
        if timeout_sec is not None:
            fields["timeout_sec"] = validate_timeout(timeout_sec)
        if model is not None:
            fields["model"] = model.strip()
        if is_default is False and current.is_default:
            raise ProviderError(
                "default_required",
                "Чтобы снять признак, назначьте провайдером по умолчанию другой")
        if is_default:
            fields["is_default"] = True
        if remove_key and api_key:
            raise ProviderError("invalid_request", "Нельзя одновременно задать и удалить ключ")
        # внешний без ключа не оставляем: иначе задачи будут падать с no_key
        will_have_key = bool(api_key) or (current.has_key and not remove_key)
        if ext and not will_have_key:
            raise ProviderError("key_required", "Для внешнего провайдера нужен API-ключ")
        result = self.repo.update_provider(provider_id, fields)
        if result == NOT_FOUND:
            raise ProviderError("not_found", "Провайдер не найден")
        if result == CONFLICT:
            raise ProviderError("name_taken", "Провайдер с таким названием уже есть")
        if api_key:
            self.store.save_llm_key(provider_id, api_key)
        elif remove_key:
            self.store.delete_llm_key(provider_id)
        return self.repo.get_provider(provider_id)

    def delete(self, provider_id: int) -> None:
        result = self.repo.delete_provider(provider_id)
        if result == NOT_FOUND:
            raise ProviderError("not_found", "Провайдер не найден")
        if result == IS_DEFAULT:
            raise ProviderError("is_default", "Нельзя удалить провайдера по умолчанию")
        if result == IN_USE:
            raise ProviderError("in_use", "У провайдера есть история задач; удалить нельзя")
        assert result == OK


def ensure_default_provider(repo: LlmRepo, settings: Settings) -> bool:
    """Создать внутреннюю Qwen по умолчанию, если провайдеров ещё нет (идемпотентно).

    URL - LLM_BASE_URL или адрес из архитектуры; ключ в БД не пишется: берётся из env
    (LLM_API_KEY) в момент вызова либо задаётся администратором в настройках.
    """
    if repo.count_providers() > 0:
        # Провайдер по умолчанию создан до появления модели в конфиге: дозаполняем имя модели.
        current = repo.get_default_provider()
        if (current is not None and not current.is_external and not current.model
                and settings.llm_model):
            repo.update_provider(current.id, {"model": settings.llm_model})
        return False
    created = repo.create_provider(
        DEFAULT_PROVIDER_NAME, settings.llm_base_url or DEFAULT_QWEN_URL,
        is_external=False, is_default=True, timeout_sec=DEFAULT_TIMEOUT_SEC,
        model=settings.llm_model,
    )
    if created is not None:
        log.info("создан провайдер LLM по умолчанию", extra={"provider_id": created})
    return created is not None
