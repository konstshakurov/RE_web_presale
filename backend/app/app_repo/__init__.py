"""Слой доступа к app_db: единственная точка SQL к app_db (роль app_rw)."""
from .pool import create_pool
from .repo import AppRepo, AppUser, AppUserDetail

__all__ = ["AppRepo", "AppUser", "AppUserDetail", "create_pool"]
