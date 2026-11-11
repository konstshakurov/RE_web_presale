"""Промпты агрегации правил клиента из комментариев эпика (KON-160).

LLM читает комментарии клиентов по правилам релизных клиентов и выдаёт тезисы с
источниками. Тезис не выдумывает: каждое утверждение привязано к id комментария.
"""
from __future__ import annotations

import json
import re

EPIC_RULES_SYSTEM = (
    "Ты - помощник бизнес-аналитика. Тебе даны комментарии клиентов (к эпику в Jira) и "
    "правила клиента из полей карточки. Выбери из комментариев важные для подготовки CV и "
    "подбора кандидатов утверждения - тезисы: требования к кандидатам, нюансы оформления CV, "
    "правила работы с клиентом. Каждый тезис: короткая формулировка (1-2 предложения) и "
    "источники - id тех комментариев, на которых построен тезис. Придумывать источники нельзя: "
    "id только из данных. Если тезис противоречит полевому правилу клиента - поставь "
    "\"contradicts_rules\": true. Не добавляй мед, оценку, рекомендации, только суть правил. "
    "Ответ - только JSON-массив объектов вида "
    '{"text": "...", "contradicts_rules": false, "sources": [{"id": "12345"}]}, '
    "без markdown-обёрток и пояснений."
)


def build_prompt(
    epic_name: str, rules: list[dict[str, str]] | None, comments: list[dict],
) -> str:
    """Текст запроса: правила полей + комментарии (as есть, но урезанные)."""
    rule_lines = "\n".join(
        f"- {r.get('name')}: {r.get('value')}" for r in (rules or [])) or "- (нет)"
    comment_lines = "\n".join(
        f"[{c.get('id')}] {c.get('author') or ''}: {_trim(str(c.get('body') or ''))}"
        for c in comments
    ) or "(комментариев нет)"
    return (
        f"Клиент: {epic_name}\n\n"
        f"Правила из полей карточки:\n{rule_lines}\n\n"
        f"Комментарии:\n{comment_lines}\n\n"
        "Составь тезисы правил клиента по системе выше."
    )


_MAX_BODY_CHARS = 2000


def _trim(body: str) -> str:
    body = " ".join(body.split())
    return body if len(body) <= _MAX_BODY_CHARS else body[: _MAX_BODY_CHARS - 1] + "…"


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_result(text: str) -> list[dict]:
    """Ответ LLM -> список тезисов; нормализует, отсекает тезисы без текста и неизвестных id.

    id присваиваются меткам Delivery в UI: ``source`` - ключ комментария (id) Jira как есть.
    """
    cleaned = _FENCE_RE.sub("", (text or "").strip())
    try:
        data = json.loads(cleaned)
    except ValueError:
        # ЛLM иногда обрамляет массив текстом: берём от первой [ до последней ]
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(cleaned[start : end + 1])
        except ValueError:
            return []
    if not isinstance(data, list):
        return []
    theses: list[dict] = []
    for item in data[:50]:
        if not isinstance(item, dict):
            continue
        text_value = str(item.get("text") or "").strip()
        if not text_value:
            continue
        sources = []
        for s in item.get("sources") or []:
            cid = str(s.get("id") or "").strip() if isinstance(s, dict) else str(s).strip()
            if cid:
                sources.append({"id": cid})
        theses.append({
            "text": text_value,
            "contradicts_rules": bool(item.get("contradicts_rules")),
            "sources": sources,
        })
    # id источников валидируются по кэшу уже в сервисе (app.epic_comment): тезисы,
    # указывающие на несуществующий комментарий, источник не получают.
    return theses
