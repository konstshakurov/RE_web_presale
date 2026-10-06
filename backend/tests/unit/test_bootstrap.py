from app.bootstrap import seed_admins


class FakeRepo:
    def __init__(self):
        self.users: dict[str, tuple[str, bool]] = {}

    def ensure_admin(self, login):
        if login in self.users:
            return False
        self.users[login] = ("admin", True)
        return True


def test_seed_creates_admins_idempotently():
    repo = FakeRepo()
    assert seed_admins(repo, ("ivan", "petr")) == 2
    assert seed_admins(repo, ("ivan", "petr")) == 0
    assert repo.users == {"ivan": ("admin", True), "petr": ("admin", True)}


def test_seed_does_not_overwrite_existing():
    repo = FakeRepo()
    repo.users["ivan"] = ("user", False)
    assert seed_admins(repo, ("ivan",)) == 0
    assert repo.users["ivan"] == ("user", False)


def test_seed_skips_too_long_login_and_empty():
    repo = FakeRepo()
    assert seed_admins(repo, ("a" * 129,)) == 0
    assert seed_admins(repo, ()) == 0
    assert repo.users == {}
