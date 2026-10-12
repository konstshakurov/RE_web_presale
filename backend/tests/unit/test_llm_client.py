"""OpenAI-совместимый клиент: запрос, заголовок ключа, маппинг ошибок без утечки текстов."""
from __future__ import annotations

import json

import httpx
import pytest

from app.llm_gateway.client import LlmCallError, LlmTimeoutError, OpenAiCompatClient

KEY = "test-client-key-0000"
URL = "https://llm.example.test/v1/"


def _client(handler):
    return OpenAiCompatClient(httpx.MockTransport(handler))


def _call(c, **kw):
    args = dict(url=URL, api_key=KEY, model="m", system="сис", prompt="запрос", timeout_sec=5)
    args.update(kw)
    return c.complete(**args)


def test_request_shape_and_response():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ответ"}}]})

    assert _call(_client(handler)) == "ответ"
    assert seen["url"] == "https://llm.example.test/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["model"] == "m" and seen["body"]["stream"] is False
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "сис"}, {"role": "user", "content": "запрос"}]


def test_no_key_no_system():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    _call(_client(handler), api_key=None, system=None)
    assert seen["auth"] is None and len(seen["body"]["messages"]) == 1


@pytest.mark.parametrize("status, code", [(401, "provider_auth"), (403, "provider_auth"),
                                          (429, "provider_error"), (500, "provider_error")])
def test_http_errors_typed_without_body(status, code):
    def handler(req):
        return httpx.Response(status, text=f"эхо запроса {KEY} запрос")

    with pytest.raises(LlmCallError) as ei:
        _call(_client(handler))
    assert ei.value.code == code
    assert KEY not in ei.value.message and "запрос" not in ei.value.message


def test_timeout_typed():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(LlmTimeoutError) as ei:
        _call(_client(handler), timeout_sec=120)
    assert ei.value.code == "timeout" and "120 с" in ei.value.message


def test_unreachable_and_bad_payloads():
    def down(req):
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(LlmCallError) as ei:
        _call(_client(down))
    assert ei.value.code == "provider_unreachable"

    for payload in ({"x": 1}, {"choices": []}, {"choices": [{"message": {"content": "  "}}]}):
        def handler(req, p=payload):
            return httpx.Response(200, json=p)

        with pytest.raises(LlmCallError) as ei:
            _call(_client(handler))
        assert ei.value.code in {"bad_response", "empty_response"}

    with pytest.raises(LlmCallError):
        _call(_client(lambda req: httpx.Response(200, text="not json")))
