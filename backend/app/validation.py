"""Общий обработчик ошибок валидации запроса (KON-68, NFR-006).

Стандартный ответ FastAPI кладёт в ``detail[].input`` введённое значение (для пустого/битого
тела - всё тело целиком), а в ``ctx`` - детали проверки. Пароль и другие введённые значения
не должны попадать в ответ (его читают прокси, клиентские логи, мониторинг браузера).
Формат ``{"detail": [{"type", "loc", "msg", ...}]}`` сохраняется, как у стандартного ответа.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_SAFE_KEYS = ("type", "loc", "msg", "url")


def _safe_ctx(ctx: Any) -> dict[str, Any] | None:
    """Из ctx остаются только числовые пределы (max_length и т.п.); строки и объекты -
    возможное эхо ввода - отбрасываются."""
    if not isinstance(ctx, dict):
        return None
    safe = {
        k: v for k, v in ctx.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    return safe or None


def sanitize_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ошибки валидации без ``input``; type/loc/msg/url как есть, ctx - только числа."""
    out: list[dict[str, Any]] = []
    for err in errors:
        item = {k: err[k] for k in _SAFE_KEYS if k in err}
        ctx = _safe_ctx(err.get("ctx"))
        if ctx:
            item["ctx"] = ctx
        out.append(item)
    return out


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"detail": jsonable_encoder(sanitize_errors(exc.errors()))}
    )


def install_validation_handler(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error_handler)
