"""Общий обработчик ошибок валидации запроса (KON-68, NFR-006).

Стандартный ответ FastAPI кладёт в ``detail[].input`` введённое значение (для пустого/битого
тела - всё тело целиком), а в ``ctx`` - детали проверки. Пароль и другие введённые значения
не должны попадать в ответ (его читают прокси, клиентские логи, мониторинг браузера).
Формат ``{"detail": [{"type", "loc", "msg", ...}]}`` сохраняется, как у стандартного ответа.
"""
from __future__ import annotations

import json
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


def _nul_error(loc: list[Any]) -> dict[str, Any]:
    return {"type": "value_error", "loc": loc,
            "msg": "Значение содержит недопустимый символ NUL (0x00)"}


def _walk_nul(value: Any, loc: list[Any]) -> list[dict[str, Any]]:
    """Пути строк JSON-тела (ключи и значения), содержащих NUL."""
    if isinstance(value, str):
        return [_nul_error(loc)] if "\x00" in value else []
    out: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and "\x00" in k:
                out.append(_nul_error(loc))
            out += _walk_nul(v, [*loc, k])
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += _walk_nul(v, [*loc, i])
    return out


async def reject_nul(request: Request) -> None:
    """Глобальная зависимость (KON-76): NUL (0x00) в query/path/JSON-теле - 422 на границе API.

    PostgreSQL не принимает NUL в text, иначе ``psycopg.DataError`` -> 500. Значение в ответ не
    попадает: только ``loc`` и фиксированный текст.
    """
    errors: list[dict[str, Any]] = []
    for name, value in request.query_params.multi_items():
        if "\x00" in name or "\x00" in value:
            errors.append(_nul_error(["query", name.replace("\x00", "")]))
    for name, value in request.path_params.items():
        if isinstance(value, str) and "\x00" in value:
            errors.append(_nul_error(["path", name]))
    if "json" in request.headers.get("content-type", ""):
        raw = await request.body()
        # дешёвый префильтр: NUL в JSON - либо сырой байт, либо экранирование \x00
        if b"\x00" in raw or rb"\u0000" in raw.lower():
            try:
                errors += _walk_nul(json.loads(raw), ["body"])
            except ValueError:  # битый JSON - это дело обычной валидации тела
                pass
    if errors:
        raise RequestValidationError(errors)


def install_validation_handler(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error_handler)
