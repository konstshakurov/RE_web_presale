# -*- coding: utf-8 -*-
"""Клиент Jira Server 8.13.0: чтение и запись без технической учётки.

Модуль реализует ровно то, что подтверждено пробой KON-30
(``input/jira/probe_raw/probe_log.jsonl`` и ``architecture/probes/``).
Всё неподтверждённое помечено прямо в коде.

Особенности, важные для требований:

* таймаут 10 с на вызов (NFR-009); повторы по умолчанию **выключены**, потому
  что повтор внутри того же лимита означал бы нарушение NFR-009;
* тела ошибок показываются как есть (FR-029), но 401 дополнительно
  различается на «сессия истекла» и «нет прав» — в 8.13.0 оба случая отдаются
  кодом 401;
* ``priority`` и ``parent`` читаются через ``.get()``: Jira их не отдаёт;
* запись выполняется от имени пользователя, чей сессионный токен передан
  в конфиг, поэтому в истории Jira действие видно как его (FR-025, UC-005).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

import httpx

from . import fields as F
from .auth import AuthStrategy, build_auth
from .config import DEFAULT_TIMEOUT, JiraConfig
from .errors import (
    JiraAuthError,
    JiraBadRequestError,
    JiraError,
    JiraPermissionError,
    JiraTimeoutError,
    JiraUnavailableError,
    error_for_response,
)
from .models import (
    Attachment,
    Comment,
    Demand,
    SearchPage,
    ServerInfo,
    Transition,
)

#: Заголовок, который Jira Server требует при загрузке вложений.
ATTACHMENT_TOKEN_HEADER = "X-Atlassian-Token"
ATTACHMENT_TOKEN_VALUE = "no-check"

#: Формат даты в JQL для поля ``updated``.
JQL_DATETIME_FORMAT = "%Y-%m-%d %H:%M"

#: Максимальный ``maxResults``, который стоит запрашивать: проверялось, что Jira
#: отдаёт до 100 за раз; за большим значением начинается усечение.
PAGE_SIZE = 100


def jql_quote(value: str) -> str:
    """Экранировать значение для JQL."""
    return '"%s"' % str(value).replace("\\", "\\\\").replace('"', '\\"')


def _assert_header_encodable(name: str, value: str) -> None:
    """Проверить, что значение заголовка кодируется в latin-1.

    ``httpx`` кодирует заголовки в latin-1 и падает ``UnicodeEncodeError``
    на не-ASCII значении. Сессионный токен и имя cookie всегда ASCII, но
    конфиг читается извне, поэтому невнятное падение внутри HTTP-слоя
    заменяется понятной ошибкой сразу на границе.
    """
    if not isinstance(value, str):
        raise TypeError("Значение заголовка %s должно быть строкой" % name)
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "Значение заголовка «%s» содержит символы вне latin-1. "
            "Сессионный токен и имя cookie должны быть ASCII." % name
        ) from exc


class JiraClient:
    """Тонкий синхронный клиент поверх ``httpx``.

    Создавать один раз на процесс: внутри пула соединений. Секреты приходят
    только из конфигурации, наружу не возвращаются.
    """

    def __init__(
        self,
        config: Optional[JiraConfig] = None,
        *,
        auth: Optional[AuthStrategy] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.config = config or JiraConfig.from_env()
        self.config.validate()
        self.auth = auth or build_auth(
            auth_mode=self.config.auth_mode,
            session_token=self.config.session_token,
            session_cookie=self.config.session_cookie,
            username=self.config.username,
            password=self.config.password,
        )
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=self.config.timeout,
            verify=self.config.verify_tls,
            follow_redirects=False,
        )
        #: Последний вызов: путь, код, длительность. Без тела и заголовков.
        self.last_call: Dict[str, Any] = {}
        #: Кэш метаданных полей и статусов; заполняется лениво.
        self._field_index: Optional[Dict[str, Mapping]] = None
        self._status_index: Optional[List[Dict[str, Any]]] = None

    # -- служебное ----------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "JiraClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.config.base_url + path

    def _headers(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "re_web_presale-jira-client/1.0",
        }
        headers.update(self.auth.headers())
        if extra:
            headers.update(extra)
        for name, value in headers.items():
            _assert_header_encodable(name, value)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Any = None,
        content: Optional[bytes] = None,
        files: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        expect_json: bool = True,
    ) -> Any:
        """Один вызов Jira с учётом таймаута и кодов ответа.

        Никаких повторов: ``retry`` истёкшего NFR-009 означал бы нарушение
        требования. Решение о повторе принимает вызывающий код.
        """
        url = self._url(path)
        started = time.perf_counter()
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                content=content,
                files=files,
                headers=self._headers(headers),
            )
        except httpx.TimeoutException as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            self.last_call = {
                "method": method,
                "path": path,
                "status": None,
                "ms": round(elapsed, 1),
                "outcome": "timeout",
            }
            raise JiraTimeoutError(
                "Превышен лимит %.0f с на вызов Jira (NFR-009)" % self.config.timeout,
                timeout=self.config.timeout,
            ) from exc
        except httpx.HTTPError as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            self.last_call = {
                "method": method,
                "path": path,
                "status": None,
                "ms": round(elapsed, 1),
                "outcome": "network",
            }
            raise JiraUnavailableError("%s: %s" % (type(exc).__name__, exc)) from exc

        elapsed = (time.perf_counter() - started) * 1000.0
        self.last_call = {
            "method": method,
            "path": path,
            "status": response.status_code,
            "ms": round(elapsed, 1),
            "bytes": len(response.content),
            "outcome": "ok" if response.status_code < 400 else "error",
        }

        if response.status_code >= 400:
            # Классификация 401 по телу живёт в error_for_response: Jira
            # отдаёт кодом 401 и «нет прав», и «сессия истекла», а это
            # противоположные действия для приложения (FR-029, FR-025).
            raise error_for_response(response, method=method, path=path)

        if not expect_json:
            return response
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            # Jira может ответить HTML на некоторые POST; это не ошибка клиента.
            return {"_raw_text": response.text[:2000]}

    def _get_json(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None)

    # ======================================================================
    # Чтение
    # ======================================================================

    def server_info(self) -> ServerInfo:
        """``GET /rest/api/2/serverInfo``. Эндпоинт анонимный — проверено пробой."""
        return ServerInfo.from_json(self._get_json("/rest/api/2/serverInfo"))

    def myself(self) -> Dict[str, Any]:
        """``GET /rest/api/2/myself`` — проверка живости сессии (FR-029).

        Именно этот вызов используется для обнаружения истёкшей сессии: он
        возвращает 401 раньше, чем любой другой.
        """
        return self._get_json("/rest/api/2/myself")

    def check_session(self) -> Dict[str, Any]:
        """Проверить сессию и вернуть безопасное описание.

        Исключение не пробрасывается: результат нужен как логическое значение.
        ``reason`` различает случаи, которые Jira отдаёт одинаковым кодом 401,
        поэтому вызывающая сторона знает, просить ли новую авторизацию (FR-025)
        или показать пользователю отказ по правам (FR-026).
        """
        try:
            data = self.myself()
        except JiraPermissionError as exc:
            # Прав не хватает: авторизация не поможет, отказ показывается как есть.
            return {
                "ok": False,
                "status": exc.status,
                "reason": exc.reason,
                "summary": exc.summary,
                "needs_reauthorization": False,
            }
        except JiraAuthError as exc:
            return {
                "ok": False,
                "status": exc.status,
                "reason": exc.reason,
                "summary": exc.summary,
                "needs_reauthorization": exc.needs_reauthorization,
            }
        except JiraError as exc:
            return {
                "ok": False,
                "status": exc.status,
                "reason": "error",
                "summary": exc.summary,
                "needs_reauthorization": False,
            }
        return {
            "ok": True,
            "status": self.last_call.get("status"),
            "reason": "ok",
            "account_type": data.get("accountType") if isinstance(data, Mapping) else None,
            "authenticated": bool(
                isinstance(data, Mapping)
                and (data.get("accountId") or data.get("name") or data.get("key"))
            ),
            "needs_reauthorization": False,
        }

    def get_issue(self, issue_key: str, fields: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """``GET /rest/api/2/issue/{key}``.

        По умолчанию запрашиваются поля из FR-028: «только нужные поля».
        Проба показала, что без ``fields`` Jira отдаёт 625–666 полей на задачу.
        """
        wanted = list(fields) if fields else list(F.DEMAND_READ_FIELDS)
        return self._get_json(
            "/rest/api/2/issue/%s" % issue_key, fields=",".join(wanted)
        )

    def get_demand(self, issue_key: str, fields: Optional[Sequence[str]] = None) -> Demand:
        payload = self.get_issue(issue_key, fields)
        demand = Demand.from_json(payload)
        if demand is None:
            raise JiraBadRequestError(
                "Ответ по %s не содержит key" % issue_key, path="/rest/api/2/issue/%s" % issue_key
            )
        return demand

    def search(
        self,
        jql: str,
        *,
        fields: Optional[Sequence[str]] = None,
        max_results: int = PAGE_SIZE,
        start_at: int = 0,
    ) -> SearchPage:
        """``GET /rest/api/2/search``. Пагинация — ``startAt`` / ``maxResults``."""
        wanted = list(fields) if fields else list(F.DEFAULT_SEARCH_FIELDS)
        payload = self._get_json(
            "/rest/api/2/search",
            jql=jql,
            fields=",".join(wanted),
            maxResults=min(int(max_results), PAGE_SIZE),
            startAt=int(start_at),
        )
        issues = payload.get("issues") if isinstance(payload, Mapping) else None
        total = payload.get("total") if isinstance(payload, Mapping) else None
        return SearchPage(
            issues=issues if isinstance(issues, list) else [],
            total=int(total) if isinstance(total, (int, float)) else None,
            start_at=int(start_at),
            max_results=int(max_results),
        )

    def search_paged(
        self,
        jql: str,
        *,
        fields: Optional[Sequence[str]] = None,
        page_size: int = PAGE_SIZE,
        max_pages: int = 200,
    ) -> Iterator[Dict[str, Any]]:
        """Обойти все страницы выдачи. Останавливается по ``total`` или ``maxPages``.

        Проверено пробой: ``total`` у эпика большой (282 задачи), обход шёл
        ``startAt`` 0 / 100 / 200 по 100 задач за страницу.
        """
        start = 0
        pages = 0
        while pages < max_pages:
            page = self.search(jql, fields=fields, max_results=page_size, start_at=start)
            if not page.issues:
                return
            for issue in page.issues:
                yield issue
            start = page.next_start_at
            pages += 1
            if page.is_last:
                return

    def search_updated_since(
        self,
        since: str,
        *,
        project: str = F.PROJECT_DEMAND,
        issuetype: Optional[str] = None,
        fields: Optional[Sequence[str]] = None,
        max_pages: int = 200,
    ) -> Iterator[Dict[str, Any]]:
        """Инкрементальный опрос по ``updated`` (FR-028, раз в 5 минут).

        ``since`` — строка ``YYYY-MM-DD HH:mm`` в формате JQL Jira.
        Сортировка по ``updated ASC`` обязательна: без неё «инкрементальный»
        обход повторно прочитает старые задачи и потеряет часть новых.
        """
        conditions = ["project = %s" % project]
        if issuetype:
            conditions.append("issuetype = %s" % issuetype)
        conditions.append("updated > %s" % jql_quote(since))
        jql = " AND ".join(conditions) + " ORDER BY updated ASC, key ASC"
        return self.search_paged(jql, fields=fields, max_pages=max_pages)

    def epic_children(
        self,
        epic_key: str,
        *,
        fields: Optional[Sequence[str]] = None,
        max_results: int = PAGE_SIZE,
        start_at: int = 0,
        extra_jql: Optional[str] = None,
    ) -> SearchPage:
        """``GET /rest/agile/1.0/epic/{key}/issue`` — дети эпика.

        Проба: подтверждено, что эндпоинт работает и его ``total`` совпадает
        с результатом JQL ``"Epic Link" = <эпик>``. Поэтому Agile API —
        предпочтительный способ обхода детей эпика, а JQL с ``parent``
        использовать нельзя: он всегда даёт 0 результатов.
        """
        wanted = list(fields) if fields else list(F.DEMAND_READ_FIELDS)
        params: Dict[str, Any] = {
            "maxResults": min(int(max_results), PAGE_SIZE),
            "startAt": int(start_at),
            "fields": ",".join(wanted),
        }
        if extra_jql:
            params["jql"] = extra_jql
        payload = self.request("GET", "/rest/agile/1.0/epic/%s/issue" % epic_key, params=params)
        issues = payload.get("issues") if isinstance(payload, Mapping) else None
        total = payload.get("total") if isinstance(payload, Mapping) else None
        return SearchPage(
            issues=issues if isinstance(issues, list) else [],
            total=int(total) if isinstance(total, (int, float)) else None,
            start_at=int(start_at),
            max_results=int(max_results),
        )

    def iter_epic_children(
        self,
        epic_key: str,
        *,
        fields: Optional[Sequence[str]] = None,
        max_pages: int = 50,
    ) -> Iterator[Dict[str, Any]]:
        """Обойти всех детей эпика через Agile API."""
        start = 0
        pages = 0
        while pages < max_pages:
            page = self.epic_children(epic_key, fields=fields, start_at=start)
            if not page.issues:
                return
            for issue in page.issues:
                yield issue
            start = page.next_start_at
            pages += 1
            if page.is_last:
                return

    def get_epic(
        self,
        epic_key: str,
        *,
        fields: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """``GET /rest/agile/1.0/epic/{key}`` — сам эпик (Dev-18).

        Тот же Agile API, что и у детей эпика, поэтому эпик и его дети
        читаются в одном стиле. Список полей настраивается вызовом: по
        умолчанию ``EPIC_READ_FIELDS``.

        Поле связи «эпик ↔ потребность» — ``customfield_10101`` (``Epic Link``);
        это канонический выбор по замыслу. Альтернатива ``customfield_11032``
        остаётся открытым вопросом владельцу процесса.
        """
        wanted = list(fields) if fields else list(F.EPIC_READ_FIELDS)
        return self.request(
            "GET",
            "/rest/agile/1.0/epic/%s" % epic_key,
            params={"fields": ",".join(wanted)},
        )

    def get_transitions(self, issue_key: str) -> List[Transition]:
        """``GET /rest/api/2/issue/{key}/transitions?expand=transitions.fields``.

        ``expand`` обязателен: без него Jira не возвращает поля перехода, а
        без них FR-026 («показывать переходы вместе с обязательными полями»)
        невыполнима.
        """
        payload = self._get_json(
            "/rest/api/2/issue/%s/transitions" % issue_key,
            expand="transitions.fields",
        )
        raw = payload.get("transitions") if isinstance(payload, Mapping) else None
        if not isinstance(raw, list):
            return []
        return [t for t in (Transition.from_json(item) for item in raw) if t is not None]

    def get_editmeta(self, issue_key: str) -> Dict[str, Any]:
        """``GET /rest/api/2/issue/{key}/editmeta`` — какие поля вообще можно менять.

        Проба: покрывает лишь 39–50 полей задачи, поэтому ``allowedValues``
        для большинства полей недоступны. Считать источником правды нельзя —
        только как дополнение к переходам.
        """
        return self._get_json("/rest/api/2/issue/%s/editmeta" % issue_key)

    def list_fields(self, *, refresh: bool = False) -> Dict[str, Mapping]:
        """``GET /rest/api/2/field`` — метаданные всех полей. Кэшируется.

        Эндпоинт анонимный и отдаёт 833 поля на этом экземпляре.
        """
        if self._field_index is None or refresh:
            raw = self._get_json("/rest/api/2/field")
            self._field_index = F.build_field_index(raw if isinstance(raw, list) else [])
        return self._field_index

    def list_statuses(self, *, refresh: bool = False) -> List[Dict[str, Any]]:
        """``GET /rest/api/2/status`` — все статусы. Кэшируется.

        Эндпоинт анонимный, отдаёт 254 статуса.
        """
        if self._status_index is None or refresh:
            raw = self._get_json("/rest/api/2/status")
            self._status_index = raw if isinstance(raw, list) else []
        return self._status_index

    def get_comments(self, issue_key: str) -> List[Comment]:
        """Комментарии задачи. Разбиение по страницам не требуется: Jira
        отдаёт комментарии одним объектом (ключ ``comment``)."""
        payload = self._get_json("/rest/api/2/issue/%s" % issue_key, fields="comment")
        data = payload.get("fields") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            return []
        block = data.get("comment")
        if not isinstance(block, Mapping):
            return []
        raw = block.get("comments")
        if not isinstance(raw, list):
            return []
        return [c for c in (Comment.from_json(item) for item in raw) if c is not None]

    def get_attachments(self, issue_key: str) -> List[Attachment]:
        """Вложения задачи.

        Проба: ``GET /rest/api/2/issue/{key}/attachment`` отдаёт 404 на Jira
        8.13.0, поэтому вложения берутся из поля задачи, а не отдельным вызовом.
        """
        payload = self._get_json("/rest/api/2/issue/%s" % issue_key, fields="attachment")
        data = payload.get("fields") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            return []
        raw = data.get("attachment")
        if not isinstance(raw, list):
            return []
        return [a for a in (Attachment.from_json(item) for item in raw) if a is not None]

    def download_attachment(self, attachment: Attachment) -> bytes:
        """Скачать вложение.

        Содержимое лежит **не** в REST: это ``/secure/attachment/{id}/{name}``,
        имя требует URL-кодирования (в выгрузке — кириллица).
        """
        if not attachment.url:
            raise JiraBadRequestError("У вложения нет URL")
        name = attachment.filename
        from urllib.parse import quote

        path = "/secure/attachment/%s/%s" % (attachment.id, quote(name))
        response = self.request("GET", path, expect_json=False)
        return response.content

    # ======================================================================
    # Запись
    # ======================================================================

    def add_comment(self, issue_key: str, body: str) -> Comment:
        """``POST /rest/api/2/issue/{key}/comment`` — добавить комментарий.

        Действие попадает в историю задачи как действие пользователя, чей
        сессионный токен передан клиенту (FR-025).
        """
        if not body or not body.strip():
            raise ValueError("Пустой комментарий отправлять нельзя")
        payload = self.request(
            "POST",
            "/rest/api/2/issue/%s/comment" % issue_key,
            json_body={"body": body},
        )
        comment = Comment.from_json(payload) if isinstance(payload, Mapping) else None
        if comment is None:
            # Jira в некоторых версиях отдаёт 201 с пустым телом.
            return Comment(id="", author=None, body=body, created=None)
        return comment

    def delete_comment(self, issue_key: str, comment_id: str) -> bool:
        """``DELETE /rest/api/2/issue/{key}/comment/{id}``.

        Нужен для откатной пробы. В истории задачи остаётся след удаления —
        полностью откатить комментарий нельзя, поэтому вызывать только с
        явного согласия пользователя.
        """
        self.request("DELETE", "/rest/api/2/issue/%s/comment/%s" % (issue_key, comment_id))
        return True

    def add_attachment(
        self,
        issue_key: str,
        *,
        filename: str,
        content: bytes,
        mime_type: str = "application/octet-stream",
        size_limit: int = 10 * 1024 * 1024,
    ) -> List[Attachment]:
        """``POST /rest/api/2/issue/{key}/attachments`` — загрузить файл.

        Требует заголовок ``X-Atlassian-Token: no-check`` — без него Jira
        отклоняет загрузку.
        """
        if not filename:
            raise ValueError("Требуется имя файла")
        if not content:
            raise ValueError("Пустой файл загружать нельзя")
        if size_limit and len(content) > size_limit:
            raise ValueError(
                "Файл %d байт превышает лимит %d байт" % (len(content), size_limit)
            )
        response = self.request(
            "POST",
            "/rest/api/2/issue/%s/attachments" % issue_key,
            files={"file": (filename, content, mime_type)},
            headers={ATTACHMENT_TOKEN_HEADER: ATTACHMENT_TOKEN_VALUE},
            expect_json=False,
        )
        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, list):
            return []
        return [a for a in (Attachment.from_json(item) for item in payload) if a is not None]

    def delete_attachment(self, issue_key: str, attachment_id: str) -> bool:
        """Удалить вложение (откатная проба). След остаётся в истории задачи."""
        self.request(
            "DELETE",
            "/rest/api/2/issue/%s/attachment/%s" % (issue_key, attachment_id),
            expect_json=False,
        )
        return True

    def find_transition(
        self,
        issue_key: str,
        *,
        transition_id: Optional[str] = None,
        to_status: Optional[str] = None,
        name_contains: Optional[str] = None,
    ) -> Optional[Transition]:
        """Найти переход по id, имени целевого статуса или названию.

        Вызывать нужно **непосредственно перед** сменой статуса: Jira
        пересчитывает доступные переходы при каждом обращении (FR-028:
        «перед записью потребность перечитывается»).
        """
        for transition in self.get_transitions(issue_key):
            if transition_id and transition.id == str(transition_id):
                return transition
            if to_status and transition.to_status and transition.to_status.name == to_status:
                return transition
            if name_contains and name_contains.lower() in transition.name.lower():
                return transition
        return None

    def missing_required_fields(
        self, transition: Transition, values: Optional[Mapping[str, Any]] = None
    ) -> List[str]:
        """Какие обязательные поля перехода не заполнены.

        Позволяет отказать до вызова Jira, а не после: Jira вернёт 400 с
        ``errors``, и пользователю будет сложно понять причину (FR-029).
        """
        provided = dict(values or {})
        missing: List[str] = []
        for spec in transition.required_fields:
            if spec.field_id in provided:
                value = provided[spec.field_id]
                if value in (None, "", [], {}):
                    missing.append(spec.field_id)
            else:
                missing.append(spec.field_id)
        return missing

    def transition(
        self,
        issue_key: str,
        transition_id: str,
        *,
        fields: Optional[Mapping[str, Any]] = None,
        comment: Optional[str] = None,
        update: Optional[Mapping[str, Any]] = None,
        validate: bool = True,
    ) -> Dict[str, Any]:
        """``POST /rest/api/2/issue/{key}/transitions`` — сменить статус.

        Тело: ``{"transition": {"id": "..."}, "fields": {...}, "update": {...}}``.

        ``comment`` отправляется через ``update.comment.add`` — это единственная
        форма, которую принимает Jira 8.13.0. Проверено пробой 2026-10-05 на
        разрешённой для записи задаче, переход 171 «Кандидатов нет»:

        ============================  =========================================
        Форма                         Ответ Jira
        ============================  =========================================
        ``fields: {"comment": "…"}``  400 ``Field does not support update
                                       'comment'``
        ``fields: {"comment": {…}}``  то же
        ``update.comment: [{add}]``   **204, переход выполнен**
        ============================  =========================================

        Комментарий здесь — не украшение: у перехода 171 он **обязателен**, но
        не объявлен в ``transitions.fields``, поэтому не виден в
        ``required_fields`` и не проверяется заранее. Без него Jira отвечает
        400 ``{"errors": {"comment": "Необходимо добавить комментарий"}}``.

        При ``validate=True`` клиент сначала перечитывает доступные переходы и
        проверяет обязательные поля. Если перехода нет, поднимается
        ``JiraBadRequestError`` **до** записи — Jira в этом случае ответила бы
        400 с невнятным текстом.
        """
        if validate:
            transition_obj = self.find_transition(issue_key, transition_id=transition_id)
            if transition_obj is None:
                raise JiraBadRequestError(
                    "Переход %s недоступен из текущего статуса задачи %s; "
                    "возможно, статус уже изменился (FR-028: перечитать перед записью)"
                    % (transition_id, issue_key),
                    path="/rest/api/2/issue/%s/transitions" % issue_key,
                )
            missing = self.missing_required_fields(transition_obj, fields)
            if missing:
                # Имя берём из описания перехода, а не из справочника полей:
                # так сообщение совпадает с тем, что Jira покажет в UI.
                known = {spec.field_id: spec.name for spec in transition_obj.fields}
                names = ", ".join(known.get(m) or F.humanize(m) for m in missing)
                raise JiraBadRequestError(
                    "Не заполнены обязательные поля перехода «%s»: %s"
                    % (transition_obj.name, names),
                    status=400,
                    field_errors={m: "required" for m in missing},
                    path="/rest/api/2/issue/%s/transitions" % issue_key,
                )
        body: Dict[str, Any] = {"transition": {"id": str(transition_id)}}
        if fields:
            body["fields"] = dict(fields)
        merged_update: Dict[str, Any] = dict(update) if update else {}
        if comment:
            # Ключ add — это операция IssueUpdateBean; строковое значение Jira
            # не принимает, поэтому тело всегда список операций.
            merged_update.setdefault("comment", []).append(
                {"add": {"body": comment}}
            )
        if merged_update:
            body["update"] = merged_update
        return self.request(
            "POST", "/rest/api/2/issue/%s/transitions" % issue_key, json_body=body
        )

    def set_fields(self, issue_key: str, fields: Mapping[str, Any]) -> bool:
        """``PUT /rest/api/2/issue/{key}`` — изменить поля задачи.

        Используется для ставки (FR-018) и назначения исполнителя (FR-009).
        Jira отвечает 204 без тела.
        """
        if not fields:
            raise ValueError("Пустой набор полей отправлять нельзя")
        self.request(
            "PUT", "/rest/api/2/issue/%s" % issue_key, json_body={"fields": dict(fields)}
        )
        return True

    def assign(self, issue_key: str, username: str) -> bool:
        """Назначить исполнителя. FR-008/FR-009: исполнитель — сам пользователь."""
        return self.set_fields(issue_key, {"assignee": {"name": username}})

    # ======================================================================
    # Диагностика
    # ======================================================================

    def diagnose(self) -> Dict[str, Any]:
        """Сводка о состоянии подключения. Только факты, без секретов."""
        report: Dict[str, Any] = {"config": self.config.safe_summary(), "checks": []}
        for label, path in (
            ("serverInfo", "/rest/api/2/serverInfo"),
            ("myself", "/rest/api/2/myself"),
            ("field", "/rest/api/2/field"),
            ("status", "/rest/api/2/status"),
        ):
            started = time.perf_counter()
            try:
                self._get_json(path)
                report["checks"].append(
                    {
                        "label": label,
                        "ok": True,
                        "status": self.last_call.get("status"),
                        "ms": round((time.perf_counter() - started) * 1000.0, 1),
                    }
                )
            except JiraError as exc:
                report["checks"].append(
                    {
                        "label": label,
                        "ok": False,
                        "status": exc.status,
                        "ms": round((time.perf_counter() - started) * 1000.0, 1),
                        "summary": exc.summary[:200],
                    }
                )
        return report


def probe_client(config: Optional[JiraConfig] = None, **kw: Any) -> JiraClient:
    """Собрать клиента из окружения — точка входа для приложения."""
    return JiraClient(config or JiraConfig.from_env(), **kw)


__all__ = [
    "ATTACHMENT_TOKEN_HEADER",
    "ATTACHMENT_TOKEN_VALUE",
    "JQL_DATETIME_FORMAT",
    "PAGE_SIZE",
    "DEFAULT_TIMEOUT",
    "JiraClient",
    "jql_quote",
    "probe_client",
]