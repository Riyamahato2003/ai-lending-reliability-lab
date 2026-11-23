"""The only four borrower evidence tools. All database access is owner-scoped."""
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from psycopg import Connection

from lending_lab.ai.schemas import (EvidenceReference, ToolEvidence, ToolName,
                                    WorkflowStatusInput, RuleTraceInput,
                                    OfferComparisonInput, ConsentRequirementsInput)
from lending_lab.policies import compare_results


def _owned_workflow(conn: Connection, workflow_id: str, borrower_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM workflows WHERE id=%s AND borrower_id=%s", (workflow_id, borrower_id)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return row


def get_workflow_status(conn: Connection, request: WorkflowStatusInput, borrower_id: str) -> ToolEvidence:
    workflow_id = request.workflow_id
    wf = _owned_workflow(conn, workflow_id, borrower_id)
    intent = conn.execute(
        "SELECT id,lender_id,idempotency_key,payload_hash,created_at FROM submission_intents WHERE workflow_id=%s",
        (workflow_id,),
    ).fetchone()
    ledger = None
    confirmed = False
    if intent:
        ledger = conn.execute(
            "SELECT payload_hash,response,created_at FROM simulator_ledger WHERE lender_id=%s AND idempotency_key=%s",
            (intent["lender_id"], intent["idempotency_key"]),
        ).fetchone()
        if ledger:
            from lending_lab.dispatch import _confirmation_error
            confirmed = _confirmation_error(dict(intent), ledger["response"], dict(ledger)) is None
    state = wf["state"]
    if confirmed:
        disposition = "confirmed"
    elif state in {"dispatching", "submission_unknown", "submitted"}:
        disposition = "uncertain"
    elif state in {"consent_revoked", "cancelled", "failed", "ineligible"}:
        disposition = "blocked"
    else:
        disposition = "pending"
    audit = conn.execute(
        "SELECT id,sequence,action,to_state,created_at FROM audit_events WHERE workflow_id=%s ORDER BY sequence DESC LIMIT 8",
        (workflow_id,),
    ).fetchall()
    refs = [EvidenceReference(kind="workflow", identifier=str(wf["id"]))]
    refs.extend(EvidenceReference(kind="audit_event", identifier=str(r["id"])) for r in audit)
    facts = {
        "workflow_id": str(wf["id"]), "state": state, "version": wf["version"],
        "created_at": wf["created_at"].isoformat(), "updated_at": wf["updated_at"].isoformat(),
        "submission": disposition, "policy_version": wf["policy_version"],
        "audit": [{"sequence": r["sequence"], "action": r["action"], "state": r["to_state"],
                   "created_at": r["created_at"].isoformat()} for r in audit],
        "intent": ({"id": str(intent["id"]), "lender_id": intent["lender_id"],
                    "created_at": intent["created_at"].isoformat()} if intent else None),
        "ledger_evidence": ({"reference": f"{intent['lender_id']}:{intent['idempotency_key']}",
                             "application_id": ledger["response"].get("simulated_application_id"),
                             "created_at": ledger["created_at"].isoformat(),
                             "confirmed": confirmed} if ledger else None),
    }
    if intent:
        refs.append(EvidenceReference(kind="dispatch_intent", identifier=str(intent["id"])))
    if confirmed:
        refs.append(EvidenceReference(kind="simulator_ledger", identifier=f"{intent['lender_id']}:{intent['idempotency_key']}"))
    return ToolEvidence(tool=ToolName.STATUS, facts=facts, references=refs,
                        uncertainty_flags=[] if confirmed or disposition != "uncertain" else ["simulator_confirmation_unavailable_or_invalid"])


def explain_rule_trace(conn: Connection, request: RuleTraceInput, borrower_id: str) -> ToolEvidence:
    workflow_id = request.workflow_id
    wf = _owned_workflow(conn, workflow_id, borrower_id)
    snapshot = conn.execute(
        "SELECT id,policy_version,input_hash,created_at FROM evaluation_snapshots WHERE workflow_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
        (workflow_id,),
    ).fetchone()
    if not snapshot:
        return ToolEvidence(tool=ToolName.RULE_TRACE,
                            facts={"available": False, "reason": "No deterministic evaluation exists yet."},
                            references=[EvidenceReference(kind="workflow", identifier=str(wf["id"]))],
                            uncertainty_flags=["evaluation_not_available"])
    rows = conn.execute(
        """SELECT r.id,d.result FROM lender_results r JOIN sensitive_lender_result_data d ON d.lender_result_id=r.id
           WHERE r.workflow_id=%s AND r.snapshot_id=%s""", (workflow_id, snapshot["id"])
    ).fetchall()
    outcomes = [dict(row["result"]) for row in rows]
    outcomes = compare_results(outcomes)
    result_ids = {str(row["result"]["lender_id"]): str(row["id"]) for row in rows}
    facts = {"available": True, "policy_version": snapshot["policy_version"],
             "snapshot_id": str(snapshot["id"]), "snapshot_created_at": snapshot["created_at"].isoformat(),
             "illustrative_only": True,
             "lenders": [{"lender_id": x["lender_id"], "eligible_under_demo_rules": x["eligible_under_demo_rules"],
                          "rule_trace": x["rules"], "offer_version": x.get("offer_version")}
                         for x in outcomes]}
    refs = [EvidenceReference(kind="evaluation_snapshot", identifier=str(snapshot["id"]))]
    refs.extend(EvidenceReference(kind="lender_result", identifier=result_ids[x["lender_id"]]) for x in outcomes)
    return ToolEvidence(tool=ToolName.RULE_TRACE, facts=facts, references=refs)


def compare_simulated_offers(conn: Connection, request: OfferComparisonInput, borrower_id: str) -> ToolEvidence:
    workflow_id = request.workflow_id
    wf = _owned_workflow(conn, workflow_id, borrower_id)
    snapshot = conn.execute(
        "SELECT id FROM evaluation_snapshots WHERE workflow_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
        (workflow_id,),
    ).fetchone()
    if not snapshot:
        return ToolEvidence(tool=ToolName.OFFERS, facts={"available": False, "offers": [],
                            "comparison_order": "sorted by estimated total repayment, then lender ID"},
                            references=[EvidenceReference(kind="workflow", identifier=str(wf["id"]))],
                            uncertainty_flags=["evaluation_not_available"])
    rows = conn.execute(
        """SELECT r.id,r.lender_id,r.offer_expires_at,d.result FROM lender_results r
           JOIN sensitive_lender_result_data d ON d.lender_result_id=r.id
           WHERE r.workflow_id=%s AND r.snapshot_id=%s""", (workflow_id, snapshot["id"])
    ).fetchall()
    ordered = compare_results([dict(x["result"]) for x in rows])
    by_lender = {x["lender_id"]: x for x in rows}
    offers = []
    refs = [EvidenceReference(kind="evaluation_snapshot", identifier=str(snapshot["id"]))]
    now = datetime.now(timezone.utc)
    for result in ordered:
        offer = result.get("offer")
        if not offer:
            continue
        row = by_lender[result["lender_id"]]
        offers.append({"lender_id": result["lender_id"], "lender_version": result["lender_version"],
                       "offer_version": result["offer_version"], "offer_expires_at": row["offer_expires_at"].isoformat(),
                       "expired": row["offer_expires_at"] <= now, "illustrative_values": offer})
        refs.append(EvidenceReference(kind="lender_offer", identifier=str(row["id"])))
    return ToolEvidence(tool=ToolName.OFFERS,
                        facts={"available": True, "currency": "INR", "minor_unit": "paise",
                               "comparison_order": "sorted by estimated total repayment, then lender ID",
                               "offers": offers, "illustrative_only": True}, references=refs)


def explain_consent_or_approval_requirements(conn: Connection, request: ConsentRequirementsInput,
                                             borrower_id: str) -> ToolEvidence:
    workflow_id = request.workflow_id
    wf = _owned_workflow(conn, workflow_id, borrower_id)
    lending = conn.execute("SELECT id,purpose,status,consent_text_version_id FROM consent_grants WHERE id=%s AND borrower_id=%s",
                           (wf["consent_grant_id"], borrower_id)).fetchone()
    text = conn.execute("SELECT version,consent_text FROM consent_text_versions WHERE id=%s",
                        (lending["consent_text_version_id"],)).fetchone() if lending and lending["consent_text_version_id"] else None
    ai = conn.execute("SELECT id,status FROM consent_grants WHERE borrower_id=%s AND purpose='ai_explanation_processing' ORDER BY created_at DESC LIMIT 1",
                      (borrower_id,)).fetchone()
    approval = conn.execute("SELECT id,status,workflow_version,approval_expires_at,offer_expires_at,invalidation_reason FROM approvals WHERE workflow_id=%s ORDER BY created_at DESC LIMIT 1",
                            (workflow_id,)).fetchone()
    now = datetime.now(timezone.utc)
    approval_status = approval["status"] if approval else "not_created"
    if approval and approval_status == "pending" and (approval["approval_expires_at"] <= now or approval["offer_expires_at"] <= now):
        approval_status = "expired"
    if wf["state"] in {"consent_revoked", "cancelled", "failed", "ineligible", "submitted", "submission_unknown", "dispatching"}:
        next_action = "No new submission action is permitted in the current workflow state."
    elif not lending or lending["status"] != "active":
        next_action = "Start a new workflow with active purpose-specific consent."
    elif wf["state"] == "compared" and approval_status in {"not_created", "invalidated", "expired"}:
        next_action = "The borrower may use the separate approval endpoint for a current offer."
    elif wf["state"] == "awaiting_approval" and approval_status == "pending":
        next_action = "Review the offer and use the separate approval consumption action if still valid."
    else:
        next_action = "Continue using the separately authorized workflow action for the current state."
    refs = [EvidenceReference(kind="workflow", identifier=str(wf["id"]))]
    if lending:
        refs.append(EvidenceReference(kind="consent_grant", identifier=str(lending["id"])))
    if approval:
        refs.append(EvidenceReference(kind="approval", identifier=str(approval["id"])))
    return ToolEvidence(tool=ToolName.CONSENT, facts={
        "lending_consent": {"purpose": lending["purpose"], "status": lending["status"],
                            "text_version": text["version"] if text else None,
                            "text": text["consent_text"] if text else None} if lending else None,
        "external_ai_consent": {"status": ai["status"], "grant_id": str(ai["id"])} if ai else {"status": "not_granted"},
        "approval_required_before_submission": True,
        "approval": ({"id": str(approval["id"]), "workflow_version": approval["workflow_version"], "status": approval_status, "expires_at": approval["approval_expires_at"].isoformat(),
                      "offer_expires_at": approval["offer_expires_at"].isoformat(),
                      "invalidation_reason": approval["invalidation_reason"]} if approval else {"status": "not_created"}),
        "next_permitted_borrower_action": next_action,
        "approval_is_authorization_for_this_assistant": False,
    }, references=refs)


TOOL_REGISTRY = {
    ToolName.STATUS: get_workflow_status,
    ToolName.RULE_TRACE: explain_rule_trace,
    ToolName.OFFERS: compare_simulated_offers,
    ToolName.CONSENT: explain_consent_or_approval_requirements,
}

TOOL_INPUTS = {
    ToolName.STATUS: WorkflowStatusInput,
    ToolName.RULE_TRACE: RuleTraceInput,
    ToolName.OFFERS: OfferComparisonInput,
    ToolName.CONSENT: ConsentRequirementsInput,
}
