"""Safety regression: synthetic local-demo accounts must never authenticate in production."""
from contextlib import contextmanager
from importlib import import_module
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from lending_lab.api import create_session
from lending_lab.auth import verify_session
from lending_lab.schemas import SessionRequest


def _forbidden_db_connection():
    pytest.fail("Production demo auth must refuse before querying the database")


def test_production_rejects_demo_login_before_db_access(monkeypatch):
    monkeypatch.setattr("lending_lab.api.get_settings", lambda: SimpleNamespace(app_mode="production"))
    monkeypatch.setattr("lending_lab.api.connect", _forbidden_db_connection)
    with pytest.raises(HTTPException) as caught:
        create_session(SessionRequest(username="borrower-001", password="borrower-demo"), None)
    assert caught.value.status_code == 404


def test_production_rejects_demo_tokens_before_db_access(monkeypatch):
    monkeypatch.setattr("lending_lab.auth.get_settings", lambda: SimpleNamespace(app_mode="production"))
    monkeypatch.setattr("lending_lab.auth.connect", _forbidden_db_connection)
    with pytest.raises(HTTPException) as caught:
        verify_session("synthetic.demo.token")
    assert caught.value.status_code == 401


def test_production_migrations_never_seed_demo_accounts(monkeypatch):
    migrate_module = import_module("lending_lab.migrate")

    class NoPendingMigrations:
        def resolve(self):
            return self

        @property
        def parent(self):
            return self

        def __truediv__(self, _name):
            return self

        def glob(self, _pattern):
            return []

    @contextmanager
    def fake_migration_connection():
        class NoSQLExpected:
            def execute(self, *_args, **_kwargs):
                pytest.fail("Unexpected SQL in no-pending-migrations production test")

        yield NoSQLExpected()

    monkeypatch.setattr(migrate_module, "Path", lambda *_args: NoPendingMigrations())
    monkeypatch.setattr(migrate_module, "connect_migrator", fake_migration_connection)
    monkeypatch.setattr(migrate_module, "get_settings", lambda: SimpleNamespace(app_mode="production"))
    monkeypatch.setattr(migrate_module, "hash_demo_password",
                        lambda *_args: pytest.fail("Production migrations must not hash seeded demo passwords"))

    migrate_module.migrate()
