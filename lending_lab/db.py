from collections.abc import Iterator
from contextlib import contextmanager
import time
from typing import Callable, TypeVar

import psycopg
from psycopg.rows import dict_row

from lending_lab.config import get_settings


def connect():
    return psycopg.connect(get_settings().database_url, row_factory=dict_row)


def connect_migrator():
    return psycopg.connect(get_settings().migration_database_url, row_factory=dict_row)


def connect_lab():
    return psycopg.connect(get_settings().lab_database_url, row_factory=dict_row)


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    with connect() as conn:
        with conn.transaction():
            yield conn


T = TypeVar("T")


def run_transaction_with_retry(operation: Callable[[psycopg.Connection], T], *,
                               connection_factory=connect, max_attempts: int = 3,
                               sleeper: Callable[[float], None] = time.sleep) -> T:
    """Retry only short rolled-back DB transactions on deadlock/serialization SQLSTATEs."""
    if not 1 <= max_attempts <= 3:
        raise ValueError("max_attempts must be between 1 and 3")
    for attempt in range(max_attempts):
        try:
            with connection_factory() as conn:
                with conn.transaction():
                    return operation(conn)
        except psycopg.Error as exc:
            if exc.sqlstate not in {"40P01", "40001"} or attempt + 1 >= max_attempts:
                raise
            sleeper(min(0.01 * (2 ** attempt), 0.04))
    raise AssertionError("transaction retry loop exhausted unexpectedly")


def append_security_event(event_type: str, correlation_id: str, *, actor_role: str | None = None,
                          actor_ref: str | None = None, resource_type: str | None = None,
                          resource_id: str | None = None, details: dict | None = None) -> None:
    """Durably append a low-PII security event outside the loan workflow audit stream."""
    from psycopg.types.json import Jsonb

    with transaction() as conn:
        conn.execute(
            "SELECT append_security_event(%s,%s,%s,%s,%s,%s,%s)",
            (event_type, actor_role, actor_ref, resource_type, resource_id,
             Jsonb(details or {}), correlation_id),
        )


def append_audit_event(conn, workflow_id: str, actor: str, action: str,
                       from_state: str | None, to_state: str | None,
                       details: dict, correlation_id: str) -> None:
    from psycopg.types.json import Jsonb

    conn.execute("SELECT append_audit_event(%s,%s,%s,%s,%s,%s,%s)",
                 (workflow_id, actor, action, from_state, to_state, Jsonb(details), correlation_id))
