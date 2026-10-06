"""Сохранение и чтение секретов в app.secret_store через app_repo."""
from __future__ import annotations

from ..app_repo import AppRepo
from .crypto import SealedSecret, open_secret, seal

KIND_JIRA_SESSION = "jira_session"


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
