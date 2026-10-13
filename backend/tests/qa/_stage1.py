"""Общие помощники тестов этапа 1 на стенде (KON-47): допущенные пользователи, синхронизация, очистка.

Тесты не оставляют мусора в БД стенда: файлы CV, задачи LLM и провайдеры, созданные тестом, удаляются
ролями приложения (``cleanup_*``); сгенерированные потребности нагрузки - ``cleanup_generated_needs``.
"""
from __future__ import annotations

import time

from ._fakes import JiraCtl, db, db_admin, wait_until
from ._stand import Session, logged_in

USERS = ("qa-user", "qa-revoked", "qa-jirablock")
FIXTURE_NEEDS = 24            # потребностей ANALYTICS типа «Аналитик» в наборе фейка Jira
FIXTURE_EMPLOYEES = 16        # карточек Epic (14 + 2 с общим логином)


def ensure_allow_list() -> Session:
    """Список допущенных в известном состоянии (как fixture ``baseline`` KON-57); возвращает админа."""
    admin = logged_in("qa-admin")
    users = admin.users()
    for extra in [u for u, d in users.items() if d["role"] == "admin" and u != "qa-admin" and d["active"]]:
        admin.patch(f"/admin/users/{extra}", json={"role": "user"})
    for name in USERS:
        admin.post("/admin/users", json={"login": name})
        admin.patch(f"/admin/users/{name}", json={"active": True, "role": "user"})
    if "qa-outsider" in users and users["qa-outsider"]["active"]:
        admin.patch("/admin/users/qa-outsider", json={"active": False})
    return admin


def refresh_both(s: Session) -> None:
    """Принудительный опрос потребностей и сотрудников токеном пользователя; ждёт успеха."""
    for scope in ("employees", "needs"):
        def _go(scope=scope):
            r = s.post("/sync/refresh", json={"scope": scope})
            return r.status_code == 200 and r.json().get("ok")
        wait_until(_go, timeout=60, interval=2, what=f"/sync/refresh {scope}")


def prepare_cache(s: Session, jira: JiraCtl | None = None) -> None:
    """Сбросить фейк Jira к исходному набору и дождаться, что кэш содержит его целиком."""
    if jira is not None:
        jira.reset()
    refresh_both(s)


def cleanup_files(employee_keys: list[str] | None = None) -> None:
    """Удалить файлы CV (и каскадом содержимое, навыки, кэш оценок), загруженные тестами.

    Актуальное основное CV защищено триггером БД (FR-040), поэтому очистка идёт суперпользователем с
    временным отключением пользовательских триггеров ровно на время DELETE."""
    with db_admin("cv_db") as c:
        c.execute("ALTER TABLE cv.employee_file DISABLE TRIGGER USER")
        try:
            if employee_keys:
                c.execute("DELETE FROM cv.employee_file WHERE employee_key = ANY(%s)", (employee_keys,))
            else:
                c.execute("DELETE FROM cv.employee_file WHERE uploaded_by LIKE 'qa-%'")
        finally:
            c.execute("ALTER TABLE cv.employee_file ENABLE TRIGGER USER")


def cleanup_matching() -> None:
    """Прогоны сопоставления и поиска (кэш оценок) - только строки тестового пользователя."""
    with db("cv_db") as c:
        for table in ("cv.match_batch", "cv.match_run", "cv.search_batch", "cv.search_result",
                      "cv.search_run", "cv.match_screen", "cv.search_screen"):
            try:
                c.execute(f"DELETE FROM {table}")
            except Exception:  # noqa: BLE001 - схема других миграций; пропуск не критичен
                pass


def cleanup_llm(provider_names: tuple[str, ...] = ()) -> None:
    """Задачи LLM тестовых пользователей и созданные тестами провайдеры."""
    with db("app_db") as c:
        c.execute("DELETE FROM app.llm_task WHERE owner_login LIKE 'qa-%'")
        for n in provider_names:
            c.execute("DELETE FROM app.llm_task WHERE provider_id IN "
                      "(SELECT id FROM app.llm_provider WHERE name = %s AND NOT is_default)", (n,))
            c.execute("DELETE FROM app.secret_store WHERE kind = 'llm_key' AND provider_id IN "
                      "(SELECT id FROM app.llm_provider WHERE name = %s AND NOT is_default)", (n,))
            c.execute("DELETE FROM app.llm_provider WHERE name = %s AND NOT is_default", (n,))


def cleanup_generated_needs() -> None:
    """Нагрузочные потребности (ключи ANALYTICS-2xxxx) из кэша; сбрасывает курсор не нужно."""
    with db("app_db") as c:
        c.execute("DELETE FROM app.jira_need_tag WHERE need_key ~ '^ANALYTICS-2[0-9]{4}$'")
        c.execute("DELETE FROM app.jira_need WHERE jira_key ~ '^ANALYTICS-2[0-9]{4}$'")


def need_by_key(s: Session, key: str) -> dict | None:
    r = s.http.get("/sync/needs", params={"q": key, "limit": 5}).json()
    return next((i for i in r["items"] if i["jira_key"] == key), None)


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, round(p / 100 * len(ordered)) - 1))
    return ordered[idx]


def timed_get(s: Session, url: str, **kw) -> tuple[float, int]:
    t0 = time.perf_counter()
    r = s.http.get(url, **kw)
    return time.perf_counter() - t0, r.status_code


def make_external_provider(admin: Session, name: str, *, key: str, timeout_sec: int = 30) -> dict:
    """«Внешний» провайдер на фейке LLM: создаётся API (ключ шифруется приложением), признак
    ``is_external`` выставляется SQL (внешний адрес обязан быть HTTPS, у стенда HTTPS нет)."""
    r = admin.post("/admin/llm/providers", json={
        "name": name, "url": "http://fake-llm:8080/v1", "is_external": False, "api_key": key,
        "timeout_sec": timeout_sec, "model": "fake-ext-model"})
    assert r.status_code == 201, r.text
    p = r.json()
    with db("app_db") as c:
        c.execute("UPDATE app.llm_provider SET is_external = true WHERE id = %s", (p["id"],))
    return p
