"""Сохранение и чтение секретов в app.secret_store через app_repo."""
from __future__ import annotations

from ..app_repo import AppRepo
from .crypto import SealedSecret, open_secret, seal

KIND_JIRA_SESSION = "jira_session"
KIND_LLM_KEY = "llm_key"


def _aad(kind: str, owner_login: str) -> bytes:
    return f"{kind}:{owner_login}".encode()


class SecretStore:
    """Шифрует перед записью и расшифровывает после чтения; открытого значения в БД нет."""

    def __init__(self, repo: AppRepo, key: bytes, key_id: str) -> None:
        self._repo = repo
        self._key_id = key_id
        self._keys = {key_id: key}

    def save_jira_session(self, owner_login: str, token: str) -> None:
        """Один токен на пользователя: upsert."""
        sealed = seal(token, self._keys[self._key_id], self._key_id,
                      _aad(KIND_JIRA_SESSION, owner_login))
        self._repo.upsert_jira_session_secret(owner_login, sealed)

    def load_jira_session(self, owner_login: str) -> str | None:
        sealed: SealedSecret | None = self._repo.get_jira_session_secret(owner_login)
        if sealed is None:
            return None
        return open_secret(sealed, self._keys, _aad(KIND_JIRA_SESSION, owner_login))

    def delete_jira_session(self, owner_login: str) -> bool:
        return self._repo.delete_jira_session_secret(owner_login)

    # --- ключи LLM-провайдеров (FR-030, NFR-006) ---

    def save_llm_key(self, provider_id: int, api_key: str) -> None:
        sealed = seal(api_key, self._keys[self._key_id], self._key_id,
                      _aad(KIND_LLM_KEY, str(provider_id)))
        self._repo.upsert_llm_key_secret(provider_id, sealed)

    def load_llm_key(self, provider_id: int) -> str | None:
        sealed = self._repo.get_llm_key_secret(provider_id)
        if sealed is None:
            return None
        return open_secret(sealed, self._keys, _aad(KIND_LLM_KEY, str(provider_id)))

    def delete_llm_key(self, provider_id: int) -> bool:
        return self._repo.delete_llm_key_secret(provider_id)

    # --- шифртекст произвольного содержимого (payload/результат задач LLM); AAD - с записью ---

    def seal_blob(self, plaintext: str, aad: str) -> SealedSecret:
        return seal(plaintext, self._keys[self._key_id], self._key_id, aad.encode())

    def open_blob(self, sealed: SealedSecret, aad: str) -> str:
        return open_secret(sealed, self._keys, aad.encode())
