"""Фейк LLM для QA-стенда (KON-47). OpenAI-совместимый ``POST /v1/chat/completions``. Только stdlib.

Ключей не проверяет; принятый заголовок Authorization сохраняется в журнале захвата (ключи в тестах
заведомо фиктивные ``FAKE_*``). Режим задаётся глобально (один процесс на стенд):
  ok        детерминированный ответ по типу запроса (навыки / скрининг / оценка / обычный текст)
  slow      ответ через ``delay`` секунд
  hang      не отвечает (держит соединение до ``delay`` секунд, по умолчанию 600)
  error500  HTTP 500
  http401   HTTP 401 (ключ отклонён)
  garbage   200, но content не JSON (для запросов разбора) / пустой content
Управление (без авторизации, только стенд; порт на loopback):
  POST /__qa/mode     {"mode": "...", "delay": сек}
  POST /__qa/reset    режим ok, журнал пуст
  GET  /__qa/captured полный журнал: [{"ts","auth","model","system","prompt","mode"}]
  GET  /__qa/stats    счётчики запросов
Журнал содержит ровно то, что ушло «по проводу» (проверка FR-023: ни одной запрещённой строки).
Данные вымышленные; в stdout пишутся только метод, путь и тип запроса.
"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"mode": "ok", "delay": 0.0}
CAPTURED: list = []
STATS = {"requests": 0, "by_kind": {}}
LOCK = threading.Lock()
MAX_CAPTURED = 2000
TECH = ("sql", "bpmn", "uml", "jira", "rest", "kafka", "postgresql", "python", "java", "oracle", "camunda",
        "swagger", "confluence", "системный анализ", "бизнес-анализ", "интеграции", "банк")
PLACEHOLDER = re.compile(r"\[(?:ПЕРСОНА|КОМПАНИЯ|ПРОЕКТ|ЛОКАЦИЯ|EMAIL|ТЕЛЕФОН|ССЫЛКА|КОНТАКТ)_\d+\]")


def kind_of(system: str, prompt: str) -> str:
    s = (system or "").lower()
    if "извлекаешь из резюме" in s:
        return "skills"
    if "быстро отобрать аналитиков" in s:
        return "screen"
    if "подобрать аналитика" in s or "найти среди сотрудников" in s:
        return "match"
    return "chat"


def _terms(text: str) -> set:
    low = text.lower()
    return {t for t in TECH if t in low}


def _blocks(prompt: str):
    """(метка, текст блока) для '### K1 ...' и общий текст до блока кандидатов."""
    parts = re.split(r"(?m)^### (K\d+)\s*$", prompt)
    head = parts[0]
    blocks = [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    return head, blocks


def answer(kind: str, system: str, prompt: str) -> str:
    if kind == "skills":
        found = sorted(_terms(prompt)) or ["системный анализ"]
        return json.dumps(found, ensure_ascii=False)
    if kind in ("screen", "match"):
        head, blocks = _blocks(prompt)
        need = _terms(head)
        out = []
        for label, text in blocks:
            have = _terms(text)
            hit = len(need & have)
            base = 35 + 12 * hit - (0 if need else 0)
            pct = max(0, min(100, base + (int(label[1:]) * 3) % 7))
            item = {"id": label, "percent": pct}
            if kind == "match":
                miss = sorted(need - have)
                item["rationale"] = (f"Совпадает по: {', '.join(sorted(need & have)) or 'общий профиль'}. "
                                     f"Оценка фейковой LLM (QA).")
                item["gaps"] = [f"Нет подтверждённого опыта: {m}" for m in miss[:5]]
            out.append(item)
        return json.dumps(out, ensure_ascii=False)
    ph = list(dict.fromkeys(PLACEHOLDER.findall(prompt)))
    return "Принято (фейковая LLM, QA)." + (" Упомянуты: " + ", ".join(ph) if ph else "")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return self._parsed

    def _read(self):
        """Тело читается всегда (keep-alive): непрочитанное тело ломает следующий запрос."""
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            self._parsed = json.loads(raw or b"{}")
        except ValueError:
            self._parsed = None

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/__qa/captured":
            with LOCK:
                return self._send(200, CAPTURED)
        if p == "/__qa/stats":
            return self._send(200, {**STATS, **STATE})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        self._read()
        if p == "/__qa/mode":
            d = self._body() or {}
            STATE.update(mode=str(d.get("mode", "ok")), delay=float(d.get("delay", 0)))
            return self._send(200, STATE)
        if p == "/__qa/reset":
            with LOCK:
                CAPTURED.clear()
                STATS.update(requests=0, by_kind={})
            STATE.update(mode="ok", delay=0.0)
            return self._send(200, STATE)
        if p not in ("/v1/chat/completions", "/chat/completions"):
            return self._send(404, {"error": "not found"})
        try:
            d = self._body()
            if d is None:
                raise ValueError
            msgs = d.get("messages") or []
            system = next((m.get("content", "") for m in msgs if m.get("role") == "system"), "")
            prompt = next((m.get("content", "") for m in reversed(msgs) if m.get("role") == "user"), "")
        except (ValueError, AttributeError):
            return self._send(400, {"error": "bad json"})
        kind = kind_of(system, prompt)
        with LOCK:
            STATS["requests"] += 1
            STATS["by_kind"][kind] = STATS["by_kind"].get(kind, 0) + 1
            CAPTURED.append({"ts": time.time(), "auth": self.headers.get("Authorization", ""),
                             "model": d.get("model"), "system": system, "prompt": prompt,
                             "mode": STATE["mode"], "kind": kind})
            del CAPTURED[:-MAX_CAPTURED]
        mode, delay = STATE["mode"], STATE["delay"]
        if mode == "slow":
            time.sleep(delay)
        elif mode == "hang":
            time.sleep(delay or 600)
        if mode == "error500":
            return self._send(500, {"error": {"message": "internal (fake, scenario)"}})
        if mode == "http401":
            return self._send(401, {"error": {"message": "invalid key (fake, scenario)"}})
        content = "это не JSON" if mode == "garbage" and kind != "chat" else answer(kind, system, prompt)
        if mode == "garbage" and kind == "chat":
            content = ""
        return self._send(200, {"id": "fake", "object": "chat.completion", "model": d.get("model"),
                                "choices": [{"index": 0, "finish_reason": "stop",
                                             "message": {"role": "assistant", "content": content}}]})

    def log_message(self, fmt, *args):
        print("fake-llm", self.command, self.path.split("?")[0], flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
