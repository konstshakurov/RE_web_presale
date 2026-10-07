"""Сессия Jira пользователя: вход, хранение токена, статус, блокировка (KON-52).

Пароль передаётся только аргументом ``establish`` и дальше в порт; здесь он не хранится и не
логируется. Токен хранится в secret_store зашифрованным ключом приложения (поверх запечатывания
``TokenVault``); ответы API его не содержат.

Статус отказа держится в памяти процесса (ДОПУЩЕНИЕ: в схеме нет колонки статуса, миграции
менять нельзя; после перезапуска статус выводится из наличия токена: есть - ok, нет - not_checked).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from ..auth.deps import SessionContext, authenticated_csrf
from ..secrets import SecretStore
from .port import (
    STATE_NOT_CHECKED,
    STATE_OK,
    STATE_UNAVAILABLE,
    JiraSessionError,
    JiraSessionPort,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JiraStatusInfo:
    state: str
    code: str = ""
    message: str = ""


class JiraSessionService:
    def __init__(self, port: JiraSessionPort, store: SecretStore) -> None:
        self._port = port
        self._store = store
        self._failures: dict[str, JiraStatusInfo] = {}
        self._lock = threading.Lock()

    def establish(self, login: str, password: str) -> JiraStatusInfo:
        """Войти в Jira и сохранить токен. Не бросает: отказ становится статусом."""
        try:
            token = self._port.login(login, password)
        except JiraSessionError as exc:
            if exc.state != STATE_UNAVAILABLE:
                # отказ Jira: старый токен не оставляем; при недоступности он мог остаться годным
                self._store.delete_jira_session(login)
            info = JiraStatusInfo(exc.state, exc.code, exc.message)
            with self._lock:
                self._failures[login] = info
            log.info("сессия Jira не создана: %s", exc.code)
            return info
        self._store.save_jira_session(login, token)
        with self._lock:
            self._failures.pop(login, None)
        log.info("сессия Jira создана")
        return JiraStatusInfo(STATE_OK)

    def after_login_hook(self, login: str, password: str) -> None:
        """Хук KON-51 (``app.state.after_login_hooks``)."""
        self.establish(login, password)

    def status(self, login: str) -> JiraStatusInfo:
        with self._lock:
            failure = self._failures.get(login)
        if failure is not None:
            return failure
        if self._store.load_jira_session(login) is not None:
            return JiraStatusInfo(STATE_OK)
        return JiraStatusInfo(STATE_NOT_CHECKED)

    def get_user_jira_token(self, login: str) -> str | None:
        """Запечатанный токен для JiraPort (синхронизация, запись). Расшифровка слоя
        приложения только в момент вызова; открытый токен знает лишь адаптер Jira."""
        return self._store.load_jira_session(login)


def require_jira_ok(
    request: Request, ctx: SessionContext = Depends(authenticated_csrf)
) -> SessionContext:
    """Зависимость для роутов, которые ходят в Jira: при отказе Jira действие блокируется,
    причина отдаётся как есть (409; 'unavailable' - 503, чтобы не путать с отказом по правам)."""
    info = request.app.state.jira_session.status(ctx.login)
    if info.state == STATE_OK:
        return ctx
    status = 503 if info.state == STATE_UNAVAILABLE else 409
    raise HTTPException(
        status_code=status,
        detail={"state": info.state, "code": info.code, "message": info.message},
    )
