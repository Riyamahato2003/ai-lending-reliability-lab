"""Direct borrower consent lifecycle and targeted synthetic-data deletion."""

from typing import Any

from fastapi import HTTPException
from psycopg import Connection
from psycopg.types.json import Jsonb

from lending_lab.clock import Clock, system_clock
from lending_lab.db import append_audit_event

ACTIVE_STATES = {"consented", "evaluating", "compared", "awaiting_approval", "submitting"}


def revoke_consent(conn: Connection, grant_id: str, borrower_id: str, actor: str,
                   correlation_id: str, clock: Clock = system_clock) -> dict[str, Any]:
    grant_locator = conn.execute(
        "SELECT id FROM consent_grants WHERE id=%s AND borrower_id=%s", (grant_id, borrower_id)
    ).fetchone()
    if not grant_locator:
        raise HTTPException(status_code=404, detail="Consent grant not found")
    # Lock order: grant, workflows by UUID, approvals by UUID, then steps by UUID.
    grant = conn.execute("SELECT * FROM lock_consent_grant(%s)", (grant_id,)).fetchone()
    workflow_ids = conn.execute("SELECT id FROM workflows WHERE consent_grant_id=%s ORDER BY id",
                                (grant_id,)).fetchall()
    workflows = []
    for row in workflow_ids:
        wf = conn.execute("SELECT * FROM workflows WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
        if wf:
            workflows.append(wf)
    ids = [wf["id"] for wf in workflows]
    approvals = []
    if ids:
        approval_ids = conn.execute("SELECT id FROM approvals WHERE workflow_id=ANY(%s) ORDER BY id",
                                    (ids,)).fetchall()
        for row in approval_ids:
            approval = conn.execute("SELECT * FROM approvals WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
            if approval:
                approvals.append(approval)
    steps = []
    if ids:
        step_ids = conn.execute("SELECT id FROM workflow_steps WHERE workflow_id=ANY(%s) ORDER BY id",
                                (ids,)).fetchall()
        for row in step_ids:
            step = conn.execute("SELECT * FROM workflow_steps WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
            if step:
                steps.append(step)

    now = clock.now()
    if grant["status"] == "active":
        conn.execute("UPDATE consent_grants SET status='revoked',revoked_at=%s WHERE id=%s", (now, grant_id))
    workflow_intents: dict[str, bool] = {}
    for wf in workflows:
        intent = conn.execute("SELECT id FROM submission_intents WHERE workflow_id=%s", (wf["id"],)).fetchone()
        workflow_intents[str(wf["id"])] = bool(intent)
    for approval in approvals:
        if approval["status"] in {"pending", "consumed", "expired"}:
            reason = ("consent_revoked_reconciliation_only" if workflow_intents[str(approval["workflow_id"])]
                      else "consent_revoked_before_dispatch")
            conn.execute(
                """UPDATE approvals SET status='invalidated',invalidation_reason=%s,
                   canonical_payload=NULL,offer_amount_minor=NULL,term_months=NULL,offer_values=NULL WHERE id=%s""",
                (reason, approval["id"]),
            )
        conn.execute("DELETE FROM sensitive_approval_data WHERE approval_id=%s", (approval["id"],))

    for wf in workflows:
        previous, target = wf["state"], wf["state"]
        has_intent = workflow_intents[str(wf["id"])]
        if not has_intent and previous in ACTIVE_STATES:
            target = "consent_revoked"
        elif has_intent and previous == "dispatching":
            target = "submission_unknown"
        if target != previous:
            conn.execute("UPDATE workflows SET state=%s,version=version+1,updated_at=%s WHERE id=%s",
                         (target, now, wf["id"]))
        action = "consent.revoked_reconciliation_only" if has_intent else "consent.revoked"
        append_audit_event(conn, wf["id"], actor, action, previous, target,
                           {"consent_grant_id": str(grant_id), "purpose": grant["purpose"],
                            "consent_text_version_id": str(grant["consent_text_version_id"])
                            if grant["consent_text_version_id"] else None,
                            "sensitive_payloads_erased": True}, correlation_id)

    for step in steps:
        if step["status"] in {"queued", "leased"}:
            workflow_id = str(step["workflow_id"])
            status = "dead_letter" if workflow_intents[workflow_id] else "failed"
            result = {"reason": "consent_revoked",
                      "effect": "reconcile_only" if workflow_intents[workflow_id] else "not_dispatched"}
            conn.execute(
                """UPDATE workflow_steps SET status=%s,result=%s,lease_token=NULL,lease_expires_at=NULL,
                   dead_lettered_at=CASE WHEN %s='dead_letter' THEN %s ELSE dead_lettered_at END,
                   updated_at=%s WHERE id=%s""",
                (status, Jsonb(result), status, now, now, step["id"]),
            )

    if ids:
        conn.execute("DELETE FROM sensitive_dispatch_payloads WHERE intent_id IN (SELECT id FROM submission_intents WHERE workflow_id=ANY(%s))",
                     (ids,))
        conn.execute("DELETE FROM sensitive_lender_result_data WHERE lender_result_id IN (SELECT id FROM lender_results WHERE workflow_id=ANY(%s))",
                     (ids,))
        conn.execute("DELETE FROM sensitive_evaluation_snapshot_data WHERE snapshot_id IN (SELECT id FROM evaluation_snapshots WHERE workflow_id=ANY(%s))",
                     (ids,))
    remaining = conn.execute("SELECT 1 FROM consent_grants WHERE borrower_id=%s AND status='active' LIMIT 1",
                             (borrower_id,)).fetchone()
    borrower_data_deleted = not remaining
    if borrower_data_deleted:
        conn.execute("DELETE FROM sensitive_borrower_data WHERE borrower_id=%s", (borrower_id,))
    return {"consent_grant_id": str(grant_id), "status": "revoked", "affected_workflows": len(workflows),
            "borrower_data_deleted": borrower_data_deleted, "revoked_at": now}
