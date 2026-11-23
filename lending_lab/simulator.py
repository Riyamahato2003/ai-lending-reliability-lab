from typing import Any

from fastapi import HTTPException
from psycopg import Connection
from psycopg.types.json import Jsonb

from lending_lab.policies import canonical_hash


def submit_simulated(conn: Connection, lender_id: str, idempotency_key: str,
                     payload: dict[str, Any]) -> dict[str, Any]:
    """The persistent simulator ledger is the side-effect authority for this simulated lender."""
    payload_hash = canonical_hash(payload)
    existing = conn.execute("SELECT payload_hash,response FROM simulator_ledger WHERE lender_id=%s AND idempotency_key=%s",
                            (lender_id, idempotency_key)).fetchone()
    if existing:
        if existing["payload_hash"] != payload_hash:
            raise HTTPException(status_code=409, detail="Idempotency key reused with different payload")
        return existing["response"]
    intent = conn.execute(
        """SELECT i.payload_hash,w.consent_grant_id FROM submission_intents i
           JOIN workflows w ON w.id=i.workflow_id
           WHERE i.lender_id=%s AND i.idempotency_key=%s""",
        (lender_id, idempotency_key),
    ).fetchone()
    if intent:
        if intent["payload_hash"] != payload_hash:
            raise HTTPException(status_code=409, detail="Request differs from immutable dispatch intent")
        consent = conn.execute("SELECT * FROM lock_consent_grant(%s)",
                               (intent["consent_grant_id"],)).fetchone()
        if not consent or consent["status"] != "active":
            raise HTTPException(status_code=409, detail="Consent revoked before simulated effect")
    response = {"simulated_application_id": canonical_hash({"lender": lender_id, "key": idempotency_key})[:20],
                "status": "accepted_for_simulation", "lender_id": lender_id, "payload_hash": payload_hash,
                "is_real_submission": False}
    inserted = conn.execute("INSERT INTO simulator_ledger(lender_id,idempotency_key,payload_hash,response) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING lender_id",
                            (lender_id, idempotency_key, payload_hash, Jsonb(response))).fetchone()
    if not inserted:
        # The unique key serializes concurrent writers; reread the authoritative ledger result.
        existing = conn.execute("SELECT payload_hash,response FROM simulator_ledger WHERE lender_id=%s AND idempotency_key=%s",
                                (lender_id, idempotency_key)).fetchone()
        if existing and existing["payload_hash"] == payload_hash:
            return existing["response"]
        if existing:
            raise HTTPException(status_code=409, detail="Idempotency key reused with different payload")
        raise RuntimeError("Simulator ledger conflict without a persisted row")
    return response
