"""Выгрузка OpenAPI для фронтенда: ``python -m app.export_openapi`` из каталога backend/."""
from __future__ import annotations

import json
from pathlib import Path

from .main import app

PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    PATH.write_text(render(), encoding="utf-8", newline="\n")
    print("openapi.json written")
