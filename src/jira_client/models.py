# -*- coding: utf-8 -*-
"""Типы данных, которые отдаёт наружу клиент Jira.

Модели держат то, что интеграции действительно нужно, и скрывают форму
ответов Jira. Персональные данные (адреса e-mail из объектов пользователей)
в модели **не попадают** намеренно: правило пробы — не переносить e-mail
и имена людей в код, тесты и отчёты.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from . import fields as F


def _person_summary(raw: Any) -> Optional[str]:
    """Имя пользователя без e-mail и без полного набора полей.

    Jira отдаёт объект пользователя целиком, включая ``emailAddress``.
    Возвращается только отображаемое имя — этого хватает, чтобы показать
    «Назначить на Кандидата», не выгружая персональные данные дальше.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, Mapping):
        return None
    for key in ("displayName", "name", "key", "emailAddress"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@dataclass(frozen=True)
class Person:
    """Пользователь Jira в безопасном виде."""

    display_name: Optional[str]
    account_id: Optional[str] = None

    @classmethod
    def from_json(cls, raw: Any) -> Optional["Person"]:
        if isinstance(raw, str):
            # Некоторые поля Jira принимают «имя пользователя» строкой.
            name = raw.strip()
            return cls(display_name=name, account_id=name) if name else None
        if not isinstance(raw, Mapping):
            return None
        name = _person_summary(raw)
        if name is None:
            return None
        account = raw.get("accountId") or raw.get("key") or raw.get("name")
        return cls(display_name=name, account_id=str(account) if account else None)

    def __str__(self) -> str:
        return self.display_name or "неизвестно"


@dataclass(frozen=True)
class Reference:
    """Ссылка на связанный объект Jira (статус, тип задачи, приоритет)."""

    id: Optional[str]
    name: str
    #: ``statusCategory`` присутствует у статусов — полезно для группировки
    #: («Не начата» / «В работе» / «Завершена») без жёсткой привязки к именам.
    category_key: Optional[str] = None

    @classmethod
    def from_json(cls, raw: Any) -> Optional["Reference"]:
        if raw is None:
            return None
        if isinstance(raw, str):
            return cls(id=None, name=raw)
        if not isinstance(raw, Mapping):
            return None
        name = raw.get("name") or raw.get("value") or raw.get("key")
        if not isinstance(name, str) or not name.strip():
            return None
        cat = raw.get("statusCategory")
        category = None
        if isinstance(cat, Mapping) and isinstance(cat.get("key"), str):
            category = cat["key"]
        ident = raw.get("id") or raw.get("key")
        return cls(
            id=str(ident) if ident is not None else None,
            name=name.strip(),
            category_key=category,
        )


@dataclass(frozen=True)
class Attachment:
    """Вложение задачи."""

    id: str
    filename: str
    size: int
    mime_type: Optional[str]
    created: Optional[str]
    #: Относительный URL для скачивания. В 8.13.0 это не REST-эндпоинт, а
    #: ``/secure/attachment/{id}/{url-encoded-name}`` — см. _api_notes.md.
    url: Optional[str] = None

    @classmethod
    def from_json(cls, raw: Mapping) -> Optional["Attachment"]:
        if not isinstance(raw, Mapping):
            return None
        ident = raw.get("id")
        name = raw.get("filename")
        if ident is None or not isinstance(name, str):
            return None
        size = raw.get("size")
        created = raw.get("created")
        mime = raw.get("mimeType")
        return cls(
            id=str(ident),
            filename=name,
            size=int(size) if isinstance(size, (int, float)) else 0,
            mime_type=mime if isinstance(mime, str) else None,
            created=created if isinstance(created, str) else None,
            url=raw.get("content") if isinstance(raw.get("content"), str) else None,
        )


@dataclass(frozen=True)
class Comment:
    """Комментарий задачи."""

    id: str
    author: Optional[Person]
    body: str
    created: Optional[str]
    updated: Optional[str] = None

    @classmethod
    def from_json(cls, raw: Mapping) -> Optional["Comment"]:
        if not isinstance(raw, Mapping):
            return None
        ident = raw.get("id")
        if ident is None:
            return None
        body = raw.get("body")
        return cls(
            id=str(ident),
            author=Person.from_json(raw.get("author")),
            body=body if isinstance(body, str) else "",
            created=raw.get("created") if isinstance(raw.get("created"), str) else None,
            updated=raw.get("updated") if isinstance(raw.get("updated"), str) else None,
        )


@dataclass(frozen=True)
class FieldSpec:
    """Описание поля из ``transitions[].fields`` или ``editmeta``.

    ``required`` — обязательность поля на конкретном переходе. Именно это
    нужно FR-026: показывать пользователю переходы вместе с обязательными
    полями.
    """

    field_id: str
    name: str
    required: bool
    schema_type: Optional[str] = None
    allowed_values: Optional[List[Reference]] = None

    @classmethod
    def from_json(cls, raw: Mapping) -> Optional["FieldSpec"]:
        if not isinstance(raw, Mapping):
            return None
        field_id = raw.get("fieldId") or raw.get("id")
        if not isinstance(field_id, str) or not field_id.strip():
            return None
        name = raw.get("name") or raw.get("fieldId") or field_id
        schema = raw.get("schema")
        schema_type = None
        if isinstance(schema, Mapping) and schema.get("type"):
            schema_type = str(schema["type"])
        allowed_raw = raw.get("allowedValues")
        allowed: Optional[List[Reference]] = None
        if isinstance(allowed_raw, list):
            allowed = [ref for ref in (Reference.from_json(v) for v in allowed_raw) if ref]
        return cls(
            field_id=field_id.strip(),
            name=str(name),
            required=bool(raw.get("required")),
            schema_type=schema_type,
            allowed_values=allowed,
        )

    def value_hint(self) -> Optional[str]:
        """Краткая подсказка по значениям поля — для отчёта и UI."""
        if not self.allowed_values:
            return None
        return ", ".join(v.name for v in self.allowed_values[:12])


def _field_specs(raw: Any) -> List["FieldSpec"]:
    """Привести ``transitions[].fields`` к списку :class:`FieldSpec`.

    Форму на этом экземпляре установила живая проба 2026-10-05 на задаче,
    разрешённой постановкой для записи (переход id 171 «Кандидатов нет»).
    Jira 8.13.0 с ``expand=transitions.fields`` отдаёт **словарь** «fieldId →
    описание»:

        "fields": {"customfield_16100": {"required": false, "name": ...,
                                         "allowedValues": [...]}}

    Раньше код ожидал список объектов, из-за чего при обходе словаря
    ``FieldSpec.from_json`` получал строку-ключ, возвращал ``None``, и
    **все поля перехода молча терялись** — включая обязательные. Список
    тоже поддерживается: без ``expand=transitions.fields`` и в некоторых
    ответах Jira поля приходят массивом.
    """
    if isinstance(raw, Mapping):
        specs: List[FieldSpec] = []
        for key, value in raw.items():
            if isinstance(value, Mapping):
                # fieldId в значении есть, но подстраховываемся на случай,
                # когда его нет — ключ словаря и есть идентификатор поля.
                source = dict(value)
                source.setdefault("fieldId", key)
                spec = FieldSpec.from_json(source)
                if spec is not None:
                    specs.append(spec)
        return specs
    if isinstance(raw, list):
        specs = []
        for item in raw:
            spec = FieldSpec.from_json(item)
            if spec is not None:
                specs.append(spec)
        return specs
    return []


@dataclass(frozen=True)
class Transition:
    """Допустимый переход из текущего статуса задачи (FR-026)."""

    id: str
    name: str
    to_status: Optional[Reference] = None
    has_screen: bool = False
    is_global: bool = False
    is_initial: bool = False
    is_available: bool = True
    #: Обязательные поля именно на этом переходе.
    fields: List[FieldSpec] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: Mapping) -> Optional["Transition"]:
        if not isinstance(raw, Mapping):
            return None
        ident = raw.get("id")
        name = raw.get("name")
        if ident is None or not isinstance(name, str):
            return None
        specs: List[FieldSpec] = _field_specs(raw.get("fields"))
        return cls(
            id=str(ident),
            name=name.strip(),
            to_status=Reference.from_json(raw.get("to")),
            has_screen=bool(raw.get("hasScreen")),
            is_global=bool(raw.get("isGlobal")),
            is_initial=bool(raw.get("isInitial")),
            is_available=bool(raw.get("isAvailable", True)),
            fields=specs,
        )

    @property
    def required_fields(self) -> List[FieldSpec]:
        return [f for f in self.fields if f.required]

    def describe(self) -> str:
        """Строка для отчёта: «id имя -> статус [обязательные поля]»."""
        to = self.to_status.name if self.to_status else "?"
        required = self.required_fields
        if not required:
            return "%s %s -> %s" % (self.id, self.name, to)
        details = []
        for spec in required:
            hint = spec.value_hint()
            details.append("%s%s" % (spec.name, " = %s" % hint if hint else ""))
        return "%s %s -> %s [обязательно: %s]" % (self.id, self.name, to, "; ".join(details))


@dataclass(frozen=True)
class Demand:
    """Потребность в виде, нужном подбору (FR-015, FR-028)."""

    key: str
    summary: Optional[str]
    status: Optional[Reference]
    issue_type: Optional[Reference]
    epic_key: Optional[str] = None
    updated: Optional[str] = None
    created: Optional[str] = None
    assignee: Optional[Person] = None
    rate: Dict[str, Any] = field(default_factory=dict)
    rules: List[Any] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: Mapping) -> Optional["Demand"]:
        if not isinstance(raw, Mapping):
            return None
        key = raw.get("key")
        if not isinstance(key, str) or not key:
            return None
        data = raw.get("fields")
        if not isinstance(data, Mapping):
            data = {}
        epic = data.get(F.EPIC_LINK)
        rate: Dict[str, Any] = {}
        for rate_id in F.RATE_FIELDS:
            value = data.get(rate_id)
            if value not in (None, "", [], {}):
                rate[rate_id] = value
        return cls(
            key=key,
            summary=data.get("summary") if isinstance(data.get("summary"), str) else None,
            status=Reference.from_json(data.get("status")),
            issue_type=Reference.from_json(data.get("issuetype")),
            epic_key=str(epic) if isinstance(epic, str) and epic else None,
            updated=data.get("updated") if isinstance(data.get("updated"), str) else None,
            created=data.get("created") if isinstance(data.get("created"), str) else None,
            assignee=Person.from_json(data.get("assignee")),
            rate=rate,
            rules=[(fid, value, name) for fid, value, name in F.pick_filled(data, F.RATE_FIELDS)],
        )


@dataclass(frozen=True)
class SearchPage:
    """Страница результатов ``/rest/api/2/search``."""

    issues: List[Mapping[str, Any]]
    total: Optional[int]
    start_at: int
    max_results: int

    @property
    def is_last(self) -> bool:
        """Пустая страница всегда последняя.

        ``total`` Jira иногда не совпадает с реальным числом задач, а обход
        не должен из-за этого зациклиться: если страница пуста, брать больше
        нечего. Пустая выдача при ``total > startAt`` — это расхождение,
        и оно важнее, чем формальное сравнение чисел.
        """
        if not self.issues:
            return True
        if self.total is None:
            return True
        return self.start_at + len(self.issues) >= self.total

    @property
    def next_start_at(self) -> int:
        return self.start_at + len(self.issues)


@dataclass(frozen=True)
class ServerInfo:
    """Ответ ``/rest/api/2/serverInfo`` — версия и базовый URL."""

    base_url: Optional[str]
    version: Optional[str]
    deployment_type: Optional[str]

    @classmethod
    def from_json(cls, raw: Mapping) -> "ServerInfo":
        if not isinstance(raw, Mapping):
            return cls(None, None, None)
        return cls(
            base_url=raw.get("baseUrl") if isinstance(raw.get("baseUrl"), str) else None,
            version=raw.get("version") if isinstance(raw.get("version"), str) else None,
            deployment_type=(
                raw.get("deploymentType") if isinstance(raw.get("deploymentType"), str) else None
            ),
        )