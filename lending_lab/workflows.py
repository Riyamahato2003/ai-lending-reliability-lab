from typing import Any
from datetime import timedelta

from fastapi import HTTPException
from psycopg import Connection
from psycopg.types.json import Jsonb

from lending_lab.policies import LENDERS, POLICY_VERSION, canonical_hash, compare_results, evaluate
from lending_lab.db import append_audit_event
from lending_lab.schemas import WorkflowState
from lending_lab.simulator import submit_simulated
from lending_lab.clock import Clock, system_clock
from lending_lab.approvals import lock_consent_then_workflow
from lending_lab.privacy import json_hmac


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"consented", "cancelled"},
    "consented": {"evaluating", "cancelled", "consent_revoked"},
    "evaluating": {"compared", "ineligible", "failed", "cancelled", "consent_revoked"},
    "compared": {"awaiting_approval", "cancelled", "consent_revoked"},
    "awaiting_approval": {"submitting", "cancelled", "consent_revoked"},
    "submitting": {"dispatching", "failed", "cancelled", "consent_revoked"},
    "dispatching": {"submitted", "submission_unknown", "failed"},
    "submission_unknown": {"submitted", "failed"},
    "ineligible": set(), "submitted": set(), "failed": set(), "cancelled": set(), "consent_revoked": set(),
}


def transition(conn: Connection, workflow_id: str, borrower_id: str, target: WorkflowState,
               expected_version: int, actor: str, correlation_id: str) -> dict[str, Any]:
    _, row = lock_consent_then_workflow(conn, workflow_id, borrower_id)
    if row["version"] != expected_version:
        raise HTTPException(status_code=409, detail="Workflow version is stale")
    current = row["state"]
    if target.value not in ALLOWED_TRANSITIONS[current]:
        raise HTTPException(status_code=409, detail=f"Illegal workflow transition: {current} to {target.value}")
    new_version = expected_version + 1
    conn.execute("UPDATE workflows SET state=%s, version=%s, updated_at=now() WHERE id=%s",
                 (target.value, new_version, workflow_id))
    append_audit_event(conn, workflow_id, actor, "workflow.transition", current, target.value,
                       {"expected_version": expected_version}, correlation_id)
    return {"workflow_id": workflow_id, "state": target.value, "version": new_version}


def evaluate_workflow(conn: Connection, workflow_id: str, borrower_id: str, correlation_id: str,
                      clock: Clock = system_clock) -> dict[str, Any]:
    consent, row = lock_consent_then_workflow(conn, workflow_id, borrower_id)
    if row["state"] != "consented":
        raise HTTPException(status_code=409, detail="Workflow must be consented before evaluation")
    if not consent or consent["status"] != "active":
        raise HTTPException(status_code=409, detail="Active consent required")
    profile = conn.execute("SELECT input_data FROM sensitive_borrower_data WHERE borrower_id=%s", (borrower_id,)).fetchone()
    if not profile:
        raise HTTPException(status_code=409, detail="Synthetic borrower data was erased; start a new consented demo workflow")
    snapshot = dict(profile["input_data"])
    digest, _ = json_hmac("evaluation-input", snapshot)
    snapshot_row = conn.execute(
        "INSERT INTO evaluation_snapshots(workflow_id,input_hash,policy_version) VALUES (%s,%s,%s) RETURNING id",
        (workflow_id, digest, POLICY_VERSION)).fetchone()
    conn.execute("INSERT INTO sensitive_evaluation_snapshot_data(snapshot_id,input_data) VALUES (%s,%s)",
                 (snapshot_row["id"], Jsonb(snapshot)))
    conn.execute("UPDATE workflows SET state='evaluating',version=version+1,updated_at=now() WHERE id=%s", (workflow_id,))
    append_audit_event(conn, workflow_id, borrower_id, "workflow.evaluation_started", "consented", "evaluating",
                       {"snapshot_id": str(snapshot_row["id"]), "input_hash": digest, "policy_version": POLICY_VERSION},
                       correlation_id)
    outcomes = compare_results([evaluate(lender_id, snapshot) for lender_id in LENDERS])
    offer_expires_at = clock.now() + timedelta(minutes=30)
    for outcome in outcomes:
        result_row = conn.execute(
            "INSERT INTO lender_results(workflow_id,snapshot_id,lender_id,offer_expires_at) VALUES (%s,%s,%s,%s) RETURNING id",
            (workflow_id, snapshot_row["id"], outcome["lender_id"],
             offer_expires_at if outcome.get("offer") else None)).fetchone()
        conn.execute("INSERT INTO sensitive_lender_result_data(lender_result_id,result) VALUES (%s,%s)",
                     (result_row["id"], Jsonb(outcome)))
    state = "compared" if any(o["eligible_under_demo_rules"] for o in outcomes) else "ineligible"
    conn.execute("UPDATE workflows SET state=%s,version=version+1,updated_at=now() WHERE id=%s", (state, workflow_id))
    append_audit_event(conn, workflow_id, borrower_id, "workflow.evaluated", "evaluating", state,
                       {"snapshot_id": str(snapshot_row["id"]), "input_hash": digest, "policy_version": POLICY_VERSION},
                       correlation_id)
    return {"workflow_id": workflow_id, "snapshot_id": str(snapshot_row["id"]), "input_hash": digest,
            "policy_version": POLICY_VERSION, "results": outcomes}


def reconcile_unknown_submission(conn: Connection, workflow_id: str, borrower_id: str,
                                 lender_id: str, idempotency_key: str,
                                 canonical_payload: dict[str, Any]) -> dict[str, Any]:
    """Recovery contract: consult the lender ledger with the SAME key before any retry."""
    _, row = lock_consent_then_workflow(conn, workflow_id, borrower_id)
    if row["state"] != "submission_unknown":
        raise HTTPException(status_code=409, detail="Workflow is not awaiting uncertain submission recovery")
    ledger = conn.execute("SELECT payload_hash,response FROM simulator_ledger WHERE lender_id=%s AND idempotency_key=%s",
                          (lender_id, idempotency_key)).fetchone()
    expected_hash = canonical_hash(canonical_payload)
    if ledger:
        if ledger["payload_hash"] != expected_hash:
            raise HTTPException(status_code=409, detail="Lender ledger has a conflicting payload for this key")
        return {"action": "reconciled", "same_idempotency_key": True, "response": ledger["response"]}
    # The caller may enqueue a retry only with this exact key and immutable payload.
    return {"action": "retry_permitted", "same_idempotency_key": True,
            "idempotency_key": idempotency_key, "payload_hash": expected_hash}
