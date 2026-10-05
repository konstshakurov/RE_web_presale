"""Каркас api. Приложение пишут задачи Dev; здесь только /health."""
from fastapi import FastAPI

app = FastAPI(title="re_web_presale api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
