"""Роуты /admin/status-groups: состав групп статусов потребностей (FR-035, KON-64).
Только role=admin (как /admin/users); изменяющие запросы - с CSRF. «Прочие» и «Все статусы»
виртуальные: считаются в /sync/needs/groups, здесь не создаются и не редактируются.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from ..auth.deps import SessionContext, require_admin
from .repo import CONFLICT, NOT_FOUND, GroupRow, StatusGroupRepo
from .rules import KNOWN_JIRA_STATUSES, RESERVED_NAMES, is_reserved, other_statuses

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/status-groups",
    tags=["admin"],
    responses={401: {"description": "Требуется вход"}, 403: {"description": "Не админ или CSRF"}},
)

NAME_TAKEN_MSG = "Группа с таким названием уже есть"
NOT_FOUND_MSG = "Группа не найдена"


class GroupOut(BaseModel):
    id: int
    name: str
    sort_order: int
    statuses: list[str]
    need_count: int = Field(description="Потребностей кэша в группе")


class StatusInfo(BaseModel):
    status: str
    count: int = Field(description="Потребностей с этим статусом в кэше")
    group_ids: list[int] = Field(description="Группы, в которые входит статус; пусто - «Прочие»")


class StatusGroupsOut(BaseModel):
    groups: list[GroupOut]
    statuses: list[StatusInfo] = Field(
        description="Источник выбора: 19 статусов workflow Jira и статусы из кэша")
    other: list[StatusInfo] = Field(description="Статусы, которые сейчас попадают в «Прочие»")
    other_count: int = Field(description="Потребностей в «Прочие»")
    reserved: list[str] = Field(description="Зарезервированные имена групп")


class CreateGroupRequest(BaseModel):
    name: str = Field(description="Название группы, 1..128 символов, уникальное")


class PatchGroupRequest(BaseModel):
    name: str | None = Field(None, description="Новое название, 1..128 символов")
    sort_order: int | None = Field(None, ge=0, le=10000)


class StatusRequest(BaseModel):
    status: str = Field(description="Статус Jira как есть, 1..128 символов")


class OrderRequest(BaseModel):
    ids: list[int] = Field(description="Id всех групп в нужном порядке")


def _repo(request: Request) -> StatusGroupRepo:
    return request.app.state.status_groups


def _out(g: GroupRow) -> GroupOut:
    return GroupOut(id=g.id, name=g.name, sort_order=g.sort_order, statuses=g.statuses,
                    need_count=g.need_count)


def _name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= 128:
        raise HTTPException(status_code=422, detail="Название группы: от 1 до 128 символов")
    if is_reserved(name):
        raise HTTPException(
            status_code=422,
            detail=f"Название «{name}» зарезервировано: группа виртуальная и не редактируется",
        )
    return name


def _status(raw: str) -> str:
    status = raw.strip()
    if not 1 <= len(status) <= 128:
        raise HTTPException(status_code=422, detail="Статус: от 1 до 128 символов")
    return status


def _group_or_404(repo: StatusGroupRepo, group_id: int) -> GroupRow:
    g = repo.get_group(group_id)
    if g is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_MSG)
    return g


@router.get("", response_model=StatusGroupsOut)
def list_groups(request: Request, _: SessionContext = Depends(require_admin)) -> StatusGroupsOut:
    repo = _repo(request)
    groups = repo.list_groups()
    cache = {s.status: s.count for s in repo.cache_statuses()}
    membership: dict[str, list[int]] = {}
    for g in groups:
        for s in g.statuses:
            membership.setdefault(s, []).append(g.id)
    # известные + из кэша + уже вошедшие в группы (статус могли ввести вручную)
    names = sorted({*KNOWN_JIRA_STATUSES, *cache, *membership})
    infos = [
        StatusInfo(status=s, count=cache.get(s, 0), group_ids=membership.get(s, []))
        for s in names
    ]
    other_names = set(other_statuses(names, membership))
    other = [i for i in infos if i.status in other_names]
    return StatusGroupsOut(
        groups=[_out(g) for g in groups], statuses=infos, other=other,
        other_count=sum(i.count for i in other), reserved=list(RESERVED_NAMES),
    )


@router.post("", response_model=GroupOut, status_code=201,
             responses={409: {"description": NAME_TAKEN_MSG}, 422: {}})
def create_group(
    body: CreateGroupRequest, request: Request, ctx: SessionContext = Depends(require_admin),
) -> GroupOut:
    repo = _repo(request)
    name = _name(body.name)
    if repo.name_taken(name):
        raise HTTPException(status_code=409, detail=NAME_TAKEN_MSG)
    gid = repo.create_group(name)
    if gid is None:
        raise HTTPException(status_code=409, detail=NAME_TAKEN_MSG)
    log.info("группы статусов: %s создал группу id=%s", ctx.login, gid)
    return _out(_group_or_404(repo, gid))


# порядок объявлен до /{group_id}, чтобы «order» не разбирался как id
@router.put("/order", response_model=list[GroupOut], responses={422: {}})
def set_order(
    body: OrderRequest, request: Request, ctx: SessionContext = Depends(require_admin),
) -> list[GroupOut]:
    repo = _repo(request)
    if not repo.set_order(body.ids):
        raise HTTPException(status_code=422, detail="Список должен содержать все группы по разу")
    log.info("группы статусов: %s изменил порядок", ctx.login)
    return [_out(g) for g in repo.list_groups()]


@router.patch("/{group_id}", response_model=GroupOut,
              responses={404: {}, 409: {"description": NAME_TAKEN_MSG}, 422: {}})
def patch_group(
    group_id: int, body: PatchGroupRequest, request: Request,
    ctx: SessionContext = Depends(require_admin),
) -> GroupOut:
    repo = _repo(request)
    if body.name is None and body.sort_order is None:
        raise HTTPException(status_code=422, detail="Укажите name и/или sort_order")
    _group_or_404(repo, group_id)
    name = _name(body.name) if body.name is not None else None
    if name is not None and repo.name_taken(name, except_id=group_id):
        raise HTTPException(status_code=409, detail=NAME_TAKEN_MSG)
    result = repo.update_group(group_id, name=name, sort_order=body.sort_order)
    if result == CONFLICT:
        raise HTTPException(status_code=409, detail=NAME_TAKEN_MSG)
    if result == NOT_FOUND:
        raise HTTPException(status_code=404, detail=NOT_FOUND_MSG)
    log.info("группы статусов: %s изменил группу id=%s", ctx.login, group_id)
    return _out(_group_or_404(repo, group_id))


@router.delete("/{group_id}", status_code=204, response_class=Response, responses={404: {}})
def delete_group(
    group_id: int, request: Request, ctx: SessionContext = Depends(require_admin),
) -> Response:
    if not _repo(request).delete_group(group_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND_MSG)
    log.info("группы статусов: %s удалил группу id=%s", ctx.login, group_id)
    return Response(status_code=204)


@router.post("/{group_id}/statuses", response_model=GroupOut, responses={404: {}, 422: {}})
def add_status(
    group_id: int, body: StatusRequest, request: Request,
    ctx: SessionContext = Depends(require_admin),
) -> GroupOut:
    """Добавить статус в группу; повтор - без изменений. Статус может входить в несколько групп."""
    repo = _repo(request)
    status = _status(body.status)
    _group_or_404(repo, group_id)
    if repo.add_status(group_id, status) == NOT_FOUND:
        raise HTTPException(status_code=404, detail=NOT_FOUND_MSG)
    log.info("группы статусов: %s добавил статус в группу id=%s", ctx.login, group_id)
    return _out(_group_or_404(repo, group_id))


@router.delete("/{group_id}/statuses", response_model=GroupOut, responses={404: {}, 422: {}})
def remove_status(
    group_id: int, request: Request,
    status: Annotated[str, Query(min_length=1, max_length=128)],
    ctx: SessionContext = Depends(require_admin),
) -> GroupOut:
    repo = _repo(request)
    _group_or_404(repo, group_id)
    repo.remove_status(group_id, _status(status))
    log.info("группы статусов: %s убрал статус из группы id=%s", ctx.login, group_id)
    return _out(_group_or_404(repo, group_id))
