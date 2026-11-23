from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from lending_lab.auth import issue_session, verify_session
from lending_lab.clock import FrozenClock
from lending_lab.config import get_settings
from lending_lab.schemas import Role
from lending_lab.config import Settings


def test_expired_signed_session_is_rejected_with_injected_clock():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    token, claims = issue_session("borrower-001", Role.BORROWER, "borrower-001", FrozenClock(now))
    future = FrozenClock(now + timedelta(seconds=get_settings().session_ttl_seconds + 1))
    with pytest.raises(HTTPException) as exc:
        verify_session(token, future)
    assert exc.value.status_code == 401


def test_tampered_session_signature_is_rejected_before_database_lookup():
    now = FrozenClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    token, _ = issue_session("borrower-001", Role.BORROWER, "borrower-001", now)
    body, signature = token.split(".")
    changed = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(HTTPException) as exc:
        verify_session(f"{body}.{changed}", now)
    assert exc.value.status_code == 401


def test_default_demo_secret_is_refused_outside_explicit_local_mode():
    settings = Settings(_env_file=None, app_mode="production")
    with pytest.raises(ValueError, match="sample demo secrets"):
        settings.validate_startup()


def test_local_demo_refuses_nonlocal_database_hosts():
    settings = Settings(_env_file=None, app_mode="local_demo",
                        database_url="postgresql://demo@remote.example/lab")
    with pytest.raises(ValueError, match="loopback"):
        settings.validate_startup()
