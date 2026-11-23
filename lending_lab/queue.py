from typing import Any
from uuid import uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from lending_lab.clock import Clock, system_clock

DEFAULT_MAX_ATTEMPTS = 5
MAX_LEASE_SECONDS = 300
MAX_RETRY_DELAY_SECONDS = 300


def claim_next_step(conn: Connection, lease_seconds: int = 30,
                    clock: Clock = system_clock, kind: str | None = None) -> dict[str, Any] | None:
    """Claim one queued or lease-expired row; caller must use a transaction."""
    if not 1 <= lease_seconds <= MAX_LEASE_SECONDS:
        raise ValueError(f"lease_seconds must be between 1 and {MAX_LEASE_SECONDS}")
    now = clock.now()
    conn.execute(
        """UPDATE workflow_steps SET status='dead_letter',dead_lettered_at=%s,
           lease_token=NULL,lease_expires_at=NULL,updated_at=%s
           WHERE attempts >= max_attempts AND
             (%s::text IS NULL OR kind=%s) AND
             ((status='queued' AND available_at <= %s) OR (status='leased' AND lease_expires_at < %s))""",
        (now, now, kind, kind, now, now),
    )
    row = conn.execute(
        """SELECT id FROM workflow_steps
           WHERE attempts < max_attempts AND
             (%s::text IS NULL OR kind=%s) AND
             ((status='queued' AND available_at <= %s) OR (status='leased' AND lease_expires_at < %s))
           ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1""", (kind, kind, now, now)).fetchone()
    if not row:
        return None
    token = str(uuid4())
    return conn.execute(
        """UPDATE workflow_steps SET status='leased', lease_token=%s,
           lease_expires_at=%s + (%s * interval '1 second'), attempts=attempts+1,updated_at=%s
           WHERE id=%s RETURNING *""", (token, now, lease_seconds, now, row["id"])).fetchone()


def complete_step(conn: Connection, step_id: str, lease_token: str, result: dict[str, Any],
                  clock: Clock = system_clock) -> bool:
    now = clock.now()
    row = conn.execute("UPDATE workflow_steps SET status='done',result=%s,lease_token=NULL,lease_expires_at=NULL,updated_at=%s WHERE id=%s AND status='leased' AND lease_token=%s AND lease_expires_at > %s RETURNING id",
                       (Jsonb(result), now, step_id, lease_token, now)).fetchone()
    return row is not None


def retry_step(conn: Connection, step_id: str, lease_token: str,
               clock: Clock = system_clock) -> dict[str, Any] | None:
    """Requeue with capped exponential delay or dead-letter at max_attempts."""
    now = clock.now()
    row = conn.execute(
        "SELECT attempts,max_attempts FROM workflow_steps WHERE id=%s AND status='leased' AND lease_token=%s AND lease_expires_at > %s FOR UPDATE",
        (step_id, lease_token, now),
    ).fetchone()
    if not row:
        return None
    if row["attempts"] >= row["max_attempts"]:
        conn.execute("UPDATE workflow_steps SET status='dead_letter',dead_lettered_at=%s,lease_token=NULL,lease_expires_at=NULL,updated_at=%s WHERE id=%s",
                     (now, now, step_id))
        return {"status": "dead_letter", "delay_seconds": 0}
    delay = min(2 ** max(0, row["attempts"] - 1), MAX_RETRY_DELAY_SECONDS)
    conn.execute("UPDATE workflow_steps SET status='queued',available_at=%s + (%s * interval '1 second'),lease_token=NULL,lease_expires_at=NULL,updated_at=%s WHERE id=%s",
                 (now, delay, now, step_id))
    return {"status": "queued", "delay_seconds": delay}
