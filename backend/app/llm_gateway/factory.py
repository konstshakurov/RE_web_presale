"""Сборка шлюза LLM для api и worker из конфига приложения."""
from __future__ import annotations

from dataclasses import dataclass

from psycopg_pool import ConnectionPool

from ..app_repo import AppRepo
from ..config import Settings
from ..cv.repo import CvRepo
from ..secrets import SecretStore
from .client import OpenAiCompatClient
from .dictionaries import DictionarySource
from .ner import build_ner
from .providers import ProviderService, ensure_default_provider
from .repo import LlmRepo
from .service import LlmGateway


@dataclass(frozen=True)
class LlmBundle:
    repo: LlmRepo
    gateway: LlmGateway
    providers: ProviderService
    dictionary: DictionarySource


def build_llm(
    settings: Settings, app_pool: ConnectionPool, cv_pool: ConnectionPool | None,
    *, with_ner: bool,
) -> LlmBundle:
    """``with_ner``: NER нужен там, где идёт обезличивание (api); worker только вызывает LLM."""
    repo = LlmRepo(app_pool)
    store = SecretStore(AppRepo(app_pool), settings.enc_key_bytes(), settings.enc_key_id)
    dictionary = DictionarySource(repo, CvRepo(cv_pool) if cv_pool is not None else None)
    gateway = LlmGateway(
        repo, store, dictionary, OpenAiCompatClient(),
        ner=build_ner() if with_ner else None, env_key=settings.llm_api_key,
    )
    ensure_default_provider(repo, settings)
    return LlmBundle(repo, gateway, ProviderService(repo, store), dictionary)
