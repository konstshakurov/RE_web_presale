"""Модуль matching: навыки из CV, сопоставление потребности с кандидатами, профиль (KON-38)."""
from .factory import build_matching
from .service import MatchingError, MatchingService

__all__ = ["MatchingError", "MatchingService", "build_matching"]
