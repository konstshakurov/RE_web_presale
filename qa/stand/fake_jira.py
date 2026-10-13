"""Фейк Jira для QA-стенда (KON-57/58, расширен в KON-47). Только стандартная библиотека.

Вход (``POST /rest/auth/1/session``), поведение зависит от логина:
  qa-jirablock - отказ входа (AUTHENTICATION_DENIED), пока пароль не равен FAKE_JIRA_RELOGIN_PW;
  qa-jirawrong - всегда AUTHENTICATED_FAILED (401);  qa-jiradown - 503;  прочие - успех (cookie JSESSIONID).
Чтение (нужна cookie ``fake-session-*``, иначе 401):
  GET /rest/api/2/myself
  GET /rest/api/2/search?jql=...&fields=...&startAt&maxResults   (project, issuetype, updated >, ORDER BY)
  GET /rest/api/2/issue/{key}?fields=...
  GET /rest/agile/1.0/epic/{key}            (плоский ответ, как у Jira Server)
  GET /rest/agile/1.0/epic/{key}/issue      (дети эпика со ВСЕХ проектов: проект и тип в fields)
Управление сценарием (только QA-стенд, без cookie; тел не логируются):
  POST /__qa/reset                          вернуть набор данных к исходному
  POST /__qa/status {"key","status"}        сменить статус; updated = сейчас (NFR-008)
  POST /__qa/age {"minutes"}              сдвинуть updated всех задач в прошлое (проверка инкремента)
  POST /__qa/gen {"count","start","status"} добавить N потребностей ANALYTICS (нагрузка NFR-002)
  POST /__qa/fail {"mode": "none|401|403|500", "delay": сек}  отказ чтения (search/issue/epic)
  GET  /__qa/stats                          счётчики запросов чтения
Все данные вымышленные. Набор строится кодом, детерминированно, относительно момента старта.
"""
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

RELOGIN_PW = os.environ.get("FAKE_JIRA_RELOGIN_PW", "")
DENIED_TEXT = "Login denied: confirmation in the browser is required (fake Jira, QA)"
FORBIDDEN_TEXT = "You do not have the permission to see the specified issue (fake Jira, QA)"
TZ = timezone(timedelta(hours=4))  # пояс Jira (JIRA_UTC_OFFSET_HOURS=4 по умолчанию)
UTC = timezone.utc

STATUSES = (
    "Требуется кандидат", "Поиск кандидата", "Найм", "Отказ по кандидату",
    "Интервью не пройдено", "Кандидат назначен", "Подготовка к интервью",
    "Интервью состоялось", "Интервью пройдено", "Закреплен за проектом",
    "Кандидатов нет", "Поиск партнерки", "Партнерка", "На проекте", "Скоро освобождается",
    "Работа завершена", "Не на проекте", "Неактуально", "Отложено",
)
EMP_STATUSES = (
    "Выход новичка", "Программа обучения", "Внутренний проект", "Свободен", "Коммерческий проект",
    "Ожидает подключения", "Скоро освобождается", "Недоступен для подключения", "Уволен", "Декрет",
)
LOCK = threading.Lock()
ISSUES: dict = {}          # key -> {"key", "project", "type", "fields": {...}, "updated": datetime(UTC)}
EPICS: dict = {}           # key -> {"name", "summary", "fields"}
FAIL = {"mode": "none", "delay": 0.0}
STATS = {"search": 0, "issue": 0, "epic": 0, "epic_children": 0, "denied": 0}
NOW0 = datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%Y-%m-%dT%H:%M:%S.000%z")


def _put(key, project, itype, status, summary, created, updated, **fields):
    base = {
        "summary": summary, "status": {"name": status}, "created": _iso(created),
        "assignee": None, "reporter": {"name": "qa-user"},
    }
    base.update(fields)
    ISSUES[key] = {"key": key, "project": project, "type": itype, "fields": base, "updated": updated}


def seed() -> None:
    """Набор данных (см. qa/testdata/README.md, раздел 2.1)."""
    ISSUES.clear()
    EPICS.clear()
    EPICS.update({
        # полные правила клиента (13 полей), частичные, без правил, правила + 11 комментариев-полей не нужны
        "CLN-1": {"name": "Клиент Лотос", "summary": "Лотос: банковская платформа", "fields": {
            "customfield_12000": "Рекомендации: аналитик с опытом банковской отрасли",
            "customfield_21801": "Условия для производства: две встречи в неделю",
            "customfield_20909": "Приёмка по чек-листу заказчика", "customfield_20910": "Штрафы не применяются",
            "customfield_20907": "Привлечение третьих лиц согласовывается", "customfield_20913": "PR разрешён после согласования",
            "customfield_20903": "Постановка задач через Jira заказчика", "customfield_20904": "РИД переходит заказчику",
            "customfield_20905": "Заказчик предоставляет доступ к тестовому стенду",
            "customfield_20906": "Автоприёмка через 5 рабочих дней", "customfield_14024": "Удалённо",
            "customfield_14023": "Фикс", "customfield_14205": "NDA подписано"}},
        "CLN-2": {"name": "Клиент Север", "summary": "Север: портал", "fields": {
            "customfield_12000": "Только удалённый формат", "customfield_14205": "NDA на согласовании"}},
        "CLN-3": {"name": "Клиент Волна", "summary": "Волна: без правил", "fields": {}},
    })
    now = NOW0
    # 19 потребностей: по одной на статус
    for i, st in enumerate(STATUSES):
        n = 1001 + i
        created = now - timedelta(days=60 - i)
        rate = {}
        if i % 4 == 0:
            rate = {"customfield_20809": 2500 + i * 10, "customfield_20810": {"value": "да"},
                    "customfield_20811": {"value": "руб."}, "customfield_20812": {"value": "час"},
                    "customfield_21105": {"value": "от клиента"}}
        elif i % 4 == 1:
            rate = {"customfield_20809": 40 + i, "customfield_20811": {"value": "USD"},
                    "customfield_20810": {"value": "да"}, "customfield_20812": {"value": "час"}}
        elif i % 4 == 2:
            rate = {"customfield_20809": 2800, "customfield_20810": {"value": "нет"},
                    "customfield_20811": {"value": "руб."}, "customfield_20812": {"value": "час"}}
        _put(f"ANALYTICS-{n}", "ANALYTICS", "Аналитик", st,
             f"Системный аналитик в проект Альфа-{i + 1} (банк, интеграции)",
             created, created + timedelta(days=1, hours=i),
             description=("Нужен аналитик. Опыт в банковской отрасли, REST API, SQL.\n"
                          "Требования к кандидату: 3+ года, BPMN, UML, Jira." if i % 3 else None),
             customfield_10811=("Системный анализ, REST, SQL, Kafka" if i % 2 == 0 else None),
             customfield_19201={"value": "Прямой контракт" if i % 2 == 0 else "Субподрядчик"},
             customfield_10101=("CLN-1" if i % 3 == 0 else "CLN-2" if i % 3 == 1 else None),
             **rate)
    c = now - timedelta(days=20)
    # особые случаи
    _put("ANALYTICS-1101", "ANALYTICS", "Аналитик", "Требуется кандидат", "Аналитик, ставка в евро",
         c, c + timedelta(hours=1), customfield_20809=50, customfield_20811={"value": "EUR"},
         customfield_20810={"value": "да"}, customfield_10101="CLN-1")
    _put("ANALYTICS-1102", "ANALYTICS", "Аналитик", "Поиск кандидата", "Аналитик, рубли без признака НДС",
         c, c + timedelta(hours=2), customfield_20809=3000, customfield_20811={"value": "руб."},
         customfield_10101="CLN-1")
    _put("ANALYTICS-1103", "ANALYTICS", "Аналитик", "Требуется кандидат", "Аналитик без эпика и описания",
         c, c + timedelta(hours=3))
    _put("ANALYTICS-1104", "ANALYTICS", "Аналитик", "Требуется кандидат", "Аналитик, неизвестный тип контракта",
         c, c + timedelta(hours=4), customfield_19201={"value": "Иной"}, customfield_10101="CLN-3")
    _put("ANALYTICS-1105", "ANALYTICS", "Аналитик", "Кандидат назначен", "Аналитик с привязанным сотрудником",
         c, c + timedelta(hours=5), customfield_10707={"name": "qa.emp01"}, customfield_10101="CLN-2")
    # чужие проекты и типы (BR-005): не должны попасть в кэш потребностей
    _put("QA-9001", "QA", "Тестировщик", "Требуется кандидат", "Тестировщик (другое направление)",
         c, c + timedelta(hours=1), customfield_10101="CLN-1")
    _put("BACK-9002", "BACK", "Разработчик", "Поиск кандидата", "Разработчик backend (другое направление)",
         c, c + timedelta(hours=1), customfield_10101="CLN-1")
    _put("ANALYTICS-9003", "ANALYTICS", "Баг", "Требуется кандидат", "Не потребность: тип «Баг»",
         c, c + timedelta(hours=1))
    # сотрудники: тип Epic проекта ANALYTICS
    names = ["Иванов Пётр Сергеевич", "Петрова Анна Игоревна", "Сидоров Олег Павлович",
             "Кузнецова Мария Андреевна", "Орлов Денис Николаевич", "Белова Екатерина Олеговна",
             "Громов Артём Викторович", "Данилова Софья Максимовна", "Ершов Илья Дмитриевич",
             "Жукова Алёна Романовна", "Зайцев Кирилл Антонович", "Ким Дарья Алексеевна",
             "Лебедев Тимур Русланович", "Мельник Вера Станиславовна"]
    vacations = [None, "не указано", "Запланирован", None]
    for i, nm in enumerate(names):
        st = EMP_STATUSES[i % len(EMP_STATUSES)]
        login = f"qa.emp{i + 1:02d}" if i < 12 else None  # 12-13: без логина
        extra = {"customfield_13504": f"Системный анализ, интеграции, опыт {3 + i} лет; BPMN, UML, SQL"}
        if login:
            extra["customfield_10707"] = {"name": login}
        if vacations[i % 4]:
            extra["customfield_10814"] = {"value": vacations[i % 4]}
        e = now - timedelta(days=100 - i)
        _put(f"ANALYTICS-{5001 + i}", "ANALYTICS", "Epic", st, nm, e, e + timedelta(days=2), **extra)
    # двое с одним логином: неоднозначная связь с потребностью
    _put("ANALYTICS-5101", "ANALYTICS", "Epic", "Свободен", "Двойник Один Тестович",
         now - timedelta(days=90), now - timedelta(days=88), customfield_10707={"name": "qa.dup"})
    _put("ANALYTICS-5102", "ANALYTICS", "Epic", "Свободен", "Двойник Два Тестович",
         now - timedelta(days=90), now - timedelta(days=88), customfield_10707={"name": "qa.dup"})


def _jira_dt(raw: str) -> datetime | None:
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d)$", raw.strip())
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    return datetime(y, mo, d, h, mi, tzinfo=TZ).astimezone(UTC)


def _render(issue: dict, fields: list | None) -> dict:
    f = dict(issue["fields"])
    f["updated"] = _iso(issue["updated"])
    f["project"] = {"key": issue["project"]}
    f["issuetype"] = {"name": issue["type"]}
    if fields:
        wanted = set(fields) | {"updated"}
        # project/issuetype отдаём только если запрошены (как Jira): sync просит их у детей эпика
        f = {k: v for k, v in f.items() if k in wanted}
    return {"key": issue["key"], "id": str(abs(hash(issue["key"])) % 10 ** 6), "fields": f}


def _search(jql: str) -> list:
    proj = re.search(r"project\s*=\s*([A-Za-z0-9_]+)", jql)
    itype = re.search(r'issuetype\s*=\s*"([^"]+)"', jql) or re.search(r"issuetype\s*=\s*(\w+)", jql)
    since = re.search(r'updated\s*>\s*"([^"]+)"', jql)
    since_dt = _jira_dt(since.group(1)) if since else None
    out = []
    for it in ISSUES.values():
        if proj and it["project"] != proj.group(1):
            continue
        if itype and it["type"] != itype.group(1):
            continue
        if since_dt and not it["updated"] > since_dt:
            continue
        out.append(it)
    out.sort(key=lambda i: (i["updated"], i["key"]))
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload=None, headers=None):
        body = json.dumps(payload if payload is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    # --- управление сценарием ---
    def _qa(self, method, path):
        global NOW0
        if method == "GET" and path == "/__qa/stats":
            return self._send(200, {**STATS, "issues": len(ISSUES), "fail": FAIL})
        if method != "POST":
            return self._send(404)
        data = self._json_body()
        with LOCK:
            if path == "/__qa/reset":
                FAIL.update(mode="none", delay=0.0)
                for k in STATS:
                    STATS[k] = 0
                seed()
                # reset возвращает данные к исходным, но «updated» ставит на текущий момент: иначе
                # инкрементальный опрос (курсор по updated) не заметил бы возврат статусов
                now = datetime.now(UTC)
                for n, it in enumerate(sorted(ISSUES.values(), key=lambda i: i["key"])):
                    it["updated"] = now - timedelta(milliseconds=len(ISSUES) - n)
                return self._send(200, {"issues": len(ISSUES)})
            if path == "/__qa/age":
                # сдвинуть updated всех задач в прошлое (кэш уже содержит их): проверка инкремента по курсору
                delta = timedelta(minutes=float(data.get("minutes", 10)))
                for it in ISSUES.values():
                    it["updated"] -= delta
                return self._send(200, {"issues": len(ISSUES)})
            if path == "/__qa/status":
                it = ISSUES.get(str(data.get("key")))
                if not it:
                    return self._send(404, {"errorMessages": ["no such key"]})
                it["fields"]["status"] = {"name": str(data["status"])}
                it["updated"] = datetime.now(UTC)
                return self._send(200, {"key": it["key"], "updated": _iso(it["updated"])})
            if path == "/__qa/gen":
                count, start = int(data.get("count", 100)), int(data.get("start", 20000))
                status = str(data.get("status", "Требуется кандидат"))
                now = datetime.now(UTC)
                for i in range(count):
                    created = now - timedelta(days=1 + (i % 150))
                    upd = now - timedelta(milliseconds=count - i)   # свежее курсора опроса: попадает в инкремент
                    _put(f"ANALYTICS-{start + i}", "ANALYTICS", "Аналитик",
                         STATUSES[i % len(STATUSES)] if status == "mixed" else status,
                         f"Аналитик нагрузочный {start + i}: интеграции, REST, SQL",
                         created, upd, description="Нагрузочная потребность. Требования: SQL, BPMN.",
                         customfield_10101=f"CLN-{1 + i % 3}")
                return self._send(200, {"issues": len(ISSUES)})
            if path == "/__qa/fail":
                FAIL.update(mode=str(data.get("mode", "none")), delay=float(data.get("delay", 0)))
                return self._send(200, FAIL)
        return self._send(404)

    # --- вход ---
    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/__qa/"):
            return self._qa("POST", path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"errorMessages": ["bad json"]})
        user, pw = str(data.get("username", "")), str(data.get("password", ""))
        if path != "/rest/auth/1/session":
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

    # --- чтение ---
    def _fail(self) -> bool:
        mode = FAIL["mode"]
        if FAIL["delay"]:
            time.sleep(FAIL["delay"])
        if mode == "401":
            STATS["denied"] += 1
            self._send(401, {"errorMessages": ["not authenticated (fake, scenario)"]},
                       {"X-Seraph-LoginReason": "AUTHENTICATED_FAILED"})
            return True
        if mode == "403":
            STATS["denied"] += 1
            self._send(403, {"errorMessages": [FORBIDDEN_TEXT]})
            return True
        if mode == "500":
            STATS["denied"] += 1
            self._send(500, {"errorMessages": ["Internal Server Error (fake, scenario)"]})
            return True
        return False

    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        if path.startswith("/__qa/"):
            return self._qa("GET", path)
        cookie = self.headers.get("Cookie") or ""
        if "fake-session-" not in cookie:
            return self._send(401, {"errorMessages": ["not authenticated"]})
        if path.startswith("/rest/api/2/myself"):
            return self._send(200, {"name": "qa", "active": True})
        fields = [f for f in ",".join(q.get("fields", [])).split(",") if f] or None
        if self._fail():
            return
        with LOCK:
            if path == "/rest/api/2/search":
                STATS["search"] += 1
                found = _search(unquote(q.get("jql", [""])[0]))
                start = int(q.get("startAt", ["0"])[0])
                size = min(int(q.get("maxResults", ["50"])[0]), 100)
                page = [_render(i, fields) for i in found[start:start + size]]
                return self._send(200, {"startAt": start, "maxResults": size, "total": len(found),
                                        "issues": page})
            m = re.match(r"^/rest/api/2/issue/([A-Z][A-Z0-9_]*-\d+)$", path)
            if m:
                STATS["issue"] += 1
                key = m.group(1)
                if key in ISSUES:
                    return self._send(200, _render(ISSUES[key], fields))
                if key in EPICS:
                    return self._send(200, {"key": key, "fields": {
                        "summary": EPICS[key]["summary"], **EPICS[key]["fields"]}})
                return self._send(404, {"errorMessages": ["Issue Does Not Exist"]})
            m = re.match(r"^/rest/agile/1\.0/epic/([A-Z][A-Z0-9_]*-\d+)(/issue)?$", path)
            if m:
                key, children = m.group(1), bool(m.group(2))
                if key not in EPICS:
                    return self._send(404, {"errorMessages": ["Epic Does Not Exist"]})
                if not children:
                    STATS["epic"] += 1
                    return self._send(200, {"id": 1, "key": key, "name": EPICS[key]["name"],
                                            "summary": EPICS[key]["summary"], "done": False})
                STATS["epic_children"] += 1
                kids = sorted((i for i in ISSUES.values() if i["fields"].get("customfield_10101") == key),
                              key=lambda i: i["key"])
                start = int(q.get("startAt", ["0"])[0])
                size = min(int(q.get("maxResults", ["50"])[0]), 100)
                return self._send(200, {"startAt": start, "maxResults": size, "total": len(kids),
                                        "issues": [_render(i, fields) for i in kids[start:start + size]]})
        return self._send(404, {"errorMessages": ["not found"]})

    def log_message(self, fmt, *args):  # путь без query, код; без тел и заголовков
        print("fake-jira", self.command, self.path.split("?")[0], flush=True)


if __name__ == "__main__":
    seed()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
