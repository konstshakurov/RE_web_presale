"""set_display_name на тестовой БД (нужна TEST_APP_DB_URL, миграции V001-V004); иначе пропуск."""
import os

import pytest

from app.app_repo import AppRepo, create_pool
from app.bootstrap import seed_admins

DSN = os.environ.get("TEST_APP_DB_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_APP_DB_URL не задан (стенд БД недоступен)")

LOGIN = "kon60-test-user"


@pytest.fixture()
def pool():
    p = create_pool(DSN)
    p.open(wait=True)
    yield p
    with p.connection() as c:
        c.execute("DELETE FROM app.app_user WHERE ldap_login = %s", (LOGIN,))
    p.close()


def test_set_display_name_roundtrip(pool):
    repo = AppRepo(pool)
    seed_admins(repo, (LOGIN,))
    assert repo.get_user(LOGIN).display_name is None
    repo.set_display_name(LOGIN, "Имя Один")
    assert repo.get_user(LOGIN).display_name == "Имя Один"
    repo.set_display_name(LOGIN, "  ")  # пустое не затирает
    assert repo.get_user(LOGIN).display_name == "Имя Один"
    repo.set_display_name(LOGIN, "Имя Два")
    assert repo.get_user(LOGIN).display_name == "Имя Два"
