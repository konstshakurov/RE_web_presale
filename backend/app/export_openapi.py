"""Выгрузка OpenAPI для фронтенда: ``python -m app.export_openapi`` из каталога backend/."""
from __future__ import annotations

import json
from pathlib import Path

from .main import app

PATH = Path(__file__).resolve().parent.parent / "openapi.json"

# Описание 422 FastAPI берёт из http.HTTPStatus(422).phrase, а его переименовали в Python 3.14
# ("Unprocessable Content", в 3.12 - "Unprocessable Entity"). Файл в репозитории должен
# совпадать с рендером и локально, и в CI (test_openapi_file_is_current) - фиксируем текст.
_VALIDATION = '"description": "Unprocessable Content"'


def render() -> str:
    text = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return text.replace('"description": "Unprocessable Entity"', _VALIDATION)


if __name__ == "__main__":
    PATH.write_text(render(), encoding="utf-8", newline="\n")
    print("openapi.json written")
