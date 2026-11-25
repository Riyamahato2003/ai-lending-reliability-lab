"""Fail-closed schema readiness for Railway API and durable worker.

Use the runtime database role, never migration-owner credentials.
A healthy TCP connection is insufficient if role grants or migrations are missing.
"""
import logging
import time

from lending_lab.config import get_settings
from lending_lab.db import connect

logger = logging.getLogger("lending_lab.readiness")

def check_schema_ready() -> None:
    with connect() as conn:
        row = conn.execute(
            """SELECT to_regclass('public.workflow_steps') AS queue,
                      to_regclass('public.pilot_accounts') AS accounts,
                      to_regclass('public.pilot_sessions') AS sessions,
                      to_regclass('public.pilot_login_limits') AS login_limits,
                      to_regclass('public.workflows') AS workflows"""
        ).fetchone()
        essentials = ("queue", "workflows")
        if get_settings().app_mode == "production":
            essentials += ("accounts", "sessions", "login_limits")
        if any(row.get(k) is None for k in essentials):
            raise RuntimeError("required database schema has not finished migrating")
        # Both the runtime's table grants and the schema must be present.
        conn.execute("SELECT 1 FROM workflow_steps LIMIT 1")
        if get_settings().app_mode == "production":
            conn.execute("SELECT 1 FROM pilot_accounts LIMIT 1")


def wait_for_schema(timeout_seconds: float = 120, interval_seconds: float = 2.0) -> None:
    if timeout_seconds <= 0 or interval_seconds <= 0:
        raise ValueError("readiness timeout and interval must be positive")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            check_schema_ready()
            logger.info("database schema readiness confirmed")
            return
        except Exception as error:
            # No SQL, credentials, DSN, or exception text is logged.
            if time.monotonic() >= deadline:
                raise RuntimeError("database schema readiness timeout") from error
            time.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))
