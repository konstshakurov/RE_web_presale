"""Фейк Jira для QA-стенда (KON-57/58). Только стандартная библиотека; тел запросов не логирует.

Поведение POST /rest/auth/1/session зависит от логина (пароль LDAP тот же, что уходит в Jira):
  qa-jirablock - отказ входа (AUTHENTICATION_DENIED), пока пароль не равен FAKE_JIRA_RELOGIN_PW;
  qa-jirawrong - всегда AUTHENTICATED_FAILED (401);
  qa-jiradown  - 503 (Jira недоступна);
  прочие       - успех, выдаётся сессионная cookie JSESSIONID.
GET /rest/api/2/myself - 200 для cookie fake-session-*, иначе 401.
"""
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RELOGIN_PW = os.environ.get("FAKE_JIRA_RELOGIN_PW", "")
DENIED_TEXT = "Login denied: confirmation in the browser is required (fake Jira, QA)"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload=None, headers=None):
        body = json.dumps(payload or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"errorMessages": ["bad json"]})
        user, pw = str(data.get("username", "")), str(data.get("password", ""))
        if self.path != "/rest/auth/1/session":
            return self._send(404)
        if user == "qa-jirawrong":
            return self._send(401, {"errorMessages": ["Wrong credentials"]},
                              {"X-Seraph-LoginReason": "AUTHENTICATED_FAILED"})
        if user == "qa-jirablock" and pw != RELOGIN_PW:
            return self._send(403, {"errorMessages": [DENIED_TEXT]},
                              {"X-Seraph-LoginReason": "AUTHENTICATION_DENIED"})
        if user == "qa-jiradown":
            return self._send(503, {"errorMessages": ["unavailable"]})
        value = "fake-session-" + secrets.token_hex(8)
        return self._send(200, {"session": {"name": "JSESSIONID", "value": value}})

    def do_GET(self):
        cookie = self.headers.get("Cookie") or ""
        if self.path.startswith("/rest/api/2/myself") and "fake-session-" in cookie:
            return self._send(200, {"name": "qa", "active": True})
        return self._send(401, {"errorMessages": ["not authenticated"]})

    def log_message(self, fmt, *args):  # путь и код, без тел и заголовков
        print("fake-jira", self.command, self.path.split("?")[0], flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
