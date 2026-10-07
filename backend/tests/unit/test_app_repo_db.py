"""Проверка на тестовой БД стенда: нужна TEST_APP_DB_URL (роль app_rw, миграции V001-V003).

Без переменной тесты пропускаются. Строка подключения только из env.
"""
import os
from datetime import UTC, datetime, timedelta

import pytest

from app.app_repo import AppRepo, create_pool
from app.bootstrap import seed_admins
from app.secrets import SecretStore

DSN = os.environ.get("TEST_APP_DB_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_APP_DB_URL не задан (стенд БД недоступен)")

LOGIN = "dev16-test-admin"
TOKEN = "FAKE_JSESSIONID-DB-TEST-0123456789"


@pytest.fixture()
def pool():
    p = create_pool(DSN)
    p.open(wait=True)
    yield p
    with p.connection() as c:
        c.execute("DELETE FROM app.secret_store WHERE owner_login = %s", (LOGIN,))
        c.execute("DELETE FROM app.app_session WHERE ldap_login = %s", (LOGIN,))
        c.execute("DELETE FROM app.app_user WHERE ldap_login = %s", (LOGIN,))
    p.close()


def test_seed_idempotent(pool):
    repo = AppRepo(pool)
    assert seed_admins(repo, (LOGIN,)) == 1
    assert seed_admins(repo, (LOGIN,)) == 0
    user = repo.get_user(LOGIN)
    assert user.role == "admin" and user.active is True


def test_secret_in_db_is_encrypted_and_upserted(pool):
    repo = AppRepo(pool)
    seed_admins(repo, (LOGIN,))
    store = SecretStore(repo, os.urandom(32), "k-test")
    store.save_jira_session(LOGIN, TOKEN)
    store.save_jira_session(LOGIN, TOKEN)
    with pool.connection() as c:
        rows = c.execute(
            "SELECT ciphertext, nonce, key_id FROM app.secret_store "
            "WHERE kind = 'jira_session' AND owner_login = %s", (LOGIN,)).fetchall()
    assert len(rows) == 1
    assert TOKEN.encode() not in bytes(rows[0][0]) + bytes(rows[0][1])
    assert rows[0][2] == "k-test" and len(bytes(rows[0][1])) == 12
    assert store.load_jira_session(LOGIN) == TOKEN


def test_session_lifecycle(pool):
    repo = AppRepo(pool)
    seed_admins(repo, (LOGIN,))
    h = os.urandom(32)
    repo.create_session(h, LOGIN, datetime.now(UTC) + timedelta(hours=8))
    assert repo.get_active_session(h)[0] == LOGIN
    assert repo.revoke_user_sessions(LOGIN) == 1
    assert repo.get_active_session(h) is None
