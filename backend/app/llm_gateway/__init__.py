"""Модуль llm_gateway: провайдеры, обезличивание, предпросмотр, таймаут, задачи (ADR-007)."""
from .anonymize import AnonymizationError, Anonymizer, Dictionaries, restore
from .client import OpenAiCompatClient
from .dictionaries import DictionarySource
from .providers import ProviderError, ProviderService, ensure_default_provider
from .repo import LlmRepo
from .runner import LlmRunner
from .service import GatewayError, LeakError, LlmGateway, TaskView

__all__ = [
    "AnonymizationError",
    "Anonymizer",
    "Dictionaries",
    "DictionarySource",
    "GatewayError",
    "LeakError",
    "LlmGateway",
    "LlmRepo",
    "LlmRunner",
    "OpenAiCompatClient",
    "ProviderError",
    "ProviderService",
    "TaskView",
    "ensure_default_provider",
    "restore",
]
