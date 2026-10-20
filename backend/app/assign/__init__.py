"""Назначение кандидата, статус-действия и комментарии в Jira (KON-43, этап 2)."""
from .factory import build_assign
from .service import AssignError, AssignInput, AssignService

__all__ = ["AssignError", "AssignInput", "AssignService", "build_assign"]
