"""Список допущенных на тестовой БД (KON-53): нужна TEST_APP_DB_URL, иначе пропуск."""
import os
from datetime import UTC, datetime, timedelta

import pytest

from app.app_repo import AppRepo, create_pool
from app.app_repo.repo import LAST_ADMIN, NOT_FOUND, UPDATED

DSN = os.environ.get("TEST_APP_DB_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_APP_DB_URL не задан (стенд БД недоступен)")

PREFIX = "dev19-t-"
A, U = PREFIX + "admin", PREFIX + "user"


def _clean(p):
    with p.connection() as c:
        c.execute("DELETE FROM app.app_session WHERE ldap_login LIKE %s", (PREFIX + "%",))
        c.execute("DELETE FROM app.app_user WHERE ldap_login LIKE %s", (PREFIX + "%",))


@pytest.fixture()
def repo():
    p = create_pool(DSN)
    p.open(wait=True)
    _clean(p)
    yield AppRepo(p)
    _clean(p)
    p.close()


def test_add_idempotent_and_reactivate(repo):
    u, changed = repo.add_user(U)
    assert changed and u.role == "user" and u.active
    assert repo.add_user(U) == (u, False)
    assert repo.update_user(U, active=False) == UPDATED
    u2, changed = repo.add_user(U)
    assert changed and u2.active and u2.updated_at >= u.updated_at
    assert sum(x.ldap_login == U for x in repo.list_users_detail()) == 1


def test_revoke_ends_sessions_in_db(repo):
    repo.add_user(U)
    h = os.urandom(32)
    repo.create_session(h, U, datetime.now(UTC) + timedelta(hours=1))
    assert repo.update_user(U, active=False) == UPDATED
    assert repo.get_active_session(h) is None
    assert repo.get_user_detail(U).active is False  # строка осталась


def test_not_found(repo):
    assert repo.update_user(PREFIX + "none", active=False) == NOT_FOUND


def test_last_admin_guard(repo):
    """Сценарий воспроизводим, только если тестовый админ единственный активный в БД."""
    repo.add_user(A)
    repo.update_user(A, role="admin")
    others = [
        x for x in repo.list_users_detail() if x.role == "admin" and x.active and x.ldap_login != A
    ]
    if others:
        # есть другие активные админы: понижение разрешено; сценарий «последний» не воспроизвести
        assert repo.update_user(A, role="user") == UPDATED
        assert repo.update_user(A, active=False) == UPDATED
        pytest.skip("в БД есть другие активные админы, ветка LAST_ADMIN не проверена")
    assert repo.update_user(A, active=False) == LAST_ADMIN
    assert repo.update_user(A, role="user") == LAST_ADMIN
    repo.add_user(U)
    assert repo.update_user(U, role="admin") == UPDATED
    assert repo.update_user(A, active=False) == UPDATED
    assert repo.update_user(U, active=False) == LAST_ADMIN
