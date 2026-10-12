"""OpenAI-совместимый клиент chat/completions (внутренняя Qwen, внешние провайдеры, FR-030).

Ключ уходит только в заголовок Authorization; в исключения, логи и repr не попадает.
Тексты запроса и ответа в сообщения об ошибках тоже не попадают (NFR-006).
"""
from __future__ import annotations

from typing import Protocol

import httpx


class LlmCallError(Exception):
    """Вызов провайдера не удался. ``code`` - машинный, ``message`` - для пользователя."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LlmTimeoutError(LlmCallError):
    def __init__(self, timeout_sec: float) -> None:
        super().__init__(
            "timeout",
            f"Провайдер LLM не ответил за {timeout_sec:g} с. Повторите запрос.",
        )


class ChatClient(Protocol):
    def complete(
        self, *, url: str, api_key: str | None, model: str, system: str | None,
        prompt: str, timeout_sec: float,
    ) -> str: ...


class OpenAiCompatClient:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport  # в тестах - httpx.MockTransport

    def complete(
        self, *, url: str, api_key: str | None, model: str, system: str | None,
        prompt: str, timeout_sec: float,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        endpoint = url.rstrip("/") + "/chat/completions"
        try:
            with httpx.Client(timeout=timeout_sec, transport=self._transport) as http:
                resp = http.post(
                    endpoint, headers=headers,
                    json={"model": model, "messages": messages, "stream": False},
                )
        except httpx.TimeoutException:
            raise LlmTimeoutError(timeout_sec) from None
        except httpx.HTTPError as exc:
            raise LlmCallError(
                "provider_unreachable",
                f"Провайдер LLM недоступен ({type(exc).__name__}).",
            ) from None
        if resp.status_code in (401, 403):
            raise LlmCallError(
                "provider_auth", "Провайдер LLM отклонил ключ (проверьте ключ в настройках)."
            )
        if resp.status_code >= 400:
            raise LlmCallError(
                "provider_error", f"Провайдер LLM вернул ошибку HTTP {resp.status_code}."
            )
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LlmCallError(
                "bad_response", "Провайдер LLM вернул ответ в неожиданном формате."
            ) from None
        if not isinstance(content, str) or not content.strip():
            raise LlmCallError("empty_response", "Провайдер LLM вернул пустой ответ.")
        return content
