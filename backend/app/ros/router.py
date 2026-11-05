"""API параметров ROS (FR-041, Dev-53): GET - чтение для расчёта любым пользователем,
PUT - сохранение вкладки «ROS» целиком (роль Админ). Редактирование любого параметра -
общее для всех пользователей."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth.deps import SessionContext, authenticated, require_admin

if TYPE_CHECKING:
    from .settings import AppRosSettings, StaticRosSettings

log = logging.getLogger(__name__)
router = APIRouter()


class RosSettingsOut(BaseModel):
    billable_hours: float
    indirect_cost: float
    profit_tax_rate: float
    vat_rate: float
    insurance_before: float
    insurance_after: float
    fot_limit: float


class RosSettingsIn(BaseModel):
    """Вкладка сохраняется целиком: все 7 параметров обязательны (фронт шлёт всё)."""

    billable_hours: float
    indirect_cost: float
    profit_tax_rate: float
    vat_rate: float
    insurance_before: float
    insurance_after: float
    fot_limit: float


def _settings(request: Request) -> AppRosSettings | StaticRosSettings:
    return request.app.state.ros


@router.get("/admin/ros/settings", response_model=RosSettingsOut)
def get_ros_settings(
    request: Request, _: SessionContext = Depends(authenticated)
) -> RosSettingsOut:
    """Чтение параметров модели ROS (FR-041) - нужно для расчёта, доступно любому пользователю."""
    return RosSettingsOut(**_settings(request).all())


@router.put("/admin/ros/settings", response_model=RosSettingsOut,
            responses={400: {"description": "Значение вне разрешённых границ"}})
def put_ros_settings(
    body: RosSettingsIn, request: Request, ctx: SessionContext = Depends(require_admin)
) -> RosSettingsOut:
    """Сохранение параметров (любой админ); общие для всех, читаются при каждом открытии окна."""
    try:
        out = _settings(request).set(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("ros: %s сохранил параметры модели (НДС %s%%, лимит ФОТ %s)",
             ctx.login, out["vat_rate"], out["fot_limit"])
    return RosSettingsOut(**out)
