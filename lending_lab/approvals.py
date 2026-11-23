"""Approval payload canonicalization and atomic borrower approval lifecycle."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from psycopg import Connection
from psycopg.types.json import Jsonb

from lending_lab.clock import Clock, system_clock
from lending_lab.config import get_settings
from lending_lab.db import append_audit_event

MAX_SAFE_INTEGER = (1 << 53) - 1
APPROVAL_DOMAIN = b"ai-lending-approval-v1\n"


def _validate_jcs_subset(value: Any) -> None:
    """Validate this fixed approval schema's RFC 8785-compatible JSON subset.

    Approval payload keys and strings are ASCII; numeric fields are safe integers;
    floats are forbidden. Within this schema, sorted compact JSON is JCS byte output.
    """
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("approval payload integer exceeds RFC 8785 safe range")
        return
    if isinstance(value, float):
        raise ValueError("approval payload cannot contain floating point values")
    if isinstance(value, str):
        value.encode("ascii")
        return
    if isinstance(value, list):
        for item in value:
            _validate_jcs_subset(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("approval payload object keys must be strings")
            key.encode("ascii")
            _validate_jcs_subset(item)
        return
    raise ValueError(f"unsupported approval payload type: {type(value).__name__}")


def canonical_approval_bytes(payload: dict[str, Any]) -> bytes:
    _validate_jcs_subset(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def approval_hmac(payload: dict[str, Any]) -> tuple[str, str]:
    settings = get_settings()
    digest = hmac.new(settings.approval_hmac_secret.encode(),
                      APPROVAL_DOMAIN + canonical_approval_bytes(payload), hashlib.sha256).hexdigest()
    return digest, settings.approval_hmac_key_version


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def make_approval_payload(*, approval_id: str, borrower_id: str, workflow_id: str,
                          snapshot_id: str, snapshot_hash: str, consent_grant_id: str,
                          consent_purpose: str, policy_version: str, workflow_version: int,
                          lender_id: str, lender_version: str, offer_version: str,
                          offer_amount_minor: int, term_months: int,
                          offer_values: dict[str, Any], offer_expires_at: datetime,
                          approval_expires_at: datetime, idempotency_key: str) -> dict[str, Any]:
    return {
        "approval_expires_at": _iso(approval_expires_at),
        "approval_id": approval_id,
        "borrower_id": borrower_id,
        "consent_grant_id": consent_grant_id,
        "consent_purpose": consent_purpose,
        "evaluation_snapshot_id": snapshot_id,
        "idempotency_key": idempotency_key,
        "lender_id": lender_id,
        "lender_version": lender_version,
        "offer_amount_minor": offer_amount_minor,
        "offer_expires_at": _iso(offer_expires_at),
        "offer_values": offer_values,
        "offer_version": offer_version,
        "policy_version": policy_version,
        "schema_version": "approval-v1",
        "snapshot_digest": snapshot_hash,
        "term_months": term_months,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
    }


def lock_consent_then_workflow(conn: Connection, workflow_id: str, borrower_id: str):
    """Every 3A operation uses consent -> workflow lock order."""
    locator = conn.execute(
        "SELECT consent_grant_id FROM workflows WHERE id=%s AND borrower_id=%s",
        (workflow_id, borrower_id),
    ).fetchone()
    if not locator:
        raise HTTPException(status_code=404, detail="Workflow not found")
    consent = conn.execute("SELECT * FROM lock_consent_grant(%s)",
                           (locator["consent_grant_id"],)).fetchone()
    workflow = conn.execute("SELECT * FROM workflows WHERE id=%s AND borrower_id=%s FOR UPDATE",
                            (workflow_id, borrower_id)).fetchone()
    if not workflow or not consent or str(workflow["consent_grant_id"]) != str(consent["id"]):
        raise HTTPException(status_code=404, detail="Workflow not found")
    return consent, workflow


def _approval_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return make_approval_payload(
        approval_id=str(row["id"]), borrower_id=row["borrower_id"], workflow_id=str(row["workflow_id"]),
        snapshot_id=str(row["snapshot_id"]), snapshot_hash=row["snapshot_hash"],
        consent_grant_id=str(row["consent_grant_id"]), consent_purpose=row["consent_purpose"],
        policy_version=row["policy_version"], workflow_version=row["workflow_version"],
        lender_id=row["lender_id"], lender_version=row["lender_version"], offer_version=row["offer_version"],
        offer_amount_minor=row["offer_amount_minor"], term_months=row["term_months"],
        offer_values=row["offer_values"], offer_expires_at=row["offer_expires_at"],
        approval_expires_at=row["approval_expires_at"], idempotency_key=row["idempotency_key"],
    )


def create_approval(conn: Connection, workflow_id: str, borrower_id: str, lender_id: str,
                    expected_version: int, approval_ttl_seconds: int,
                    actor: str, correlation_id: str, clock: Clock = system_clock) -> dict[str, Any]:
    consent, workflow = lock_consent_then_workflow(conn, workflow_id, borrower_id)
    if workflow["version"] != expected_version:
        raise HTTPException(status_code=409, detail="Workflow version is stale")
    if workflow["state"] != "compared":
        raise HTTPException(status_code=409, detail="Workflow must be compared before approval")
    if consent["status"] != "active" or consent["purpose"] != "illustrative_offer_comparison":
        raise HTTPException(status_code=409, detail="Active purpose-specific consent required")
    result = conn.execute(
        """SELECT sr.result,r.snapshot_id,r.offer_expires_at,s.input_hash,s.policy_version
           FROM lender_results r JOIN sensitive_lender_result_data sr ON sr.lender_result_id=r.id
           JOIN evaluation_snapshots s ON s.id=r.snapshot_id
           WHERE r.workflow_id=%s AND r.lender_id=%s ORDER BY r.created_at DESC LIMIT 1""",
        (workflow_id, lender_id),
    ).fetchone()
    if (not result or not result["result"].get("eligible_under_demo_rules") or
            not result["result"].get("offer") or not result["offer_expires_at"]):
        raise HTTPException(status_code=409, detail="No current illustrative offer is available for this lender")
    now = clock.now()
    if result["offer_expires_at"] <= now:
        raise HTTPException(status_code=409, detail="Illustrative offer has expired; evaluate again")

    new_version = workflow["version"] + 1
    conn.execute("UPDATE workflows SET state='awaiting_approval',version=%s,updated_at=%s WHERE id=%s",
                 (new_version, now, workflow_id))
    approval_id, idempotency_key = str(uuid4()), str(uuid4())
    approval_expiry = min(now + timedelta(seconds=approval_ttl_seconds), result["offer_expires_at"])
    offer = result["result"]["offer"]
    payload = make_approval_payload(
        approval_id=approval_id, borrower_id=borrower_id, workflow_id=workflow_id,
        snapshot_id=str(result["snapshot_id"]), snapshot_hash=result["input_hash"],
        consent_grant_id=str(consent["id"]), consent_purpose=consent["purpose"],
        policy_version=result["policy_version"], workflow_version=new_version,
        lender_id=lender_id, lender_version=result["result"]["lender_version"],
        offer_version=result["result"]["offer_version"], offer_amount_minor=offer["principal_minor"],
        term_months=offer["term_months"], offer_values=offer,
        offer_expires_at=result["offer_expires_at"], approval_expires_at=approval_expiry,
        idempotency_key=idempotency_key,
    )
    digest, key_version = approval_hmac(payload)
    canonical = canonical_approval_bytes(payload).decode("ascii")
    conn.execute(
        """INSERT INTO approvals(id,borrower_id,workflow_id,snapshot_id,snapshot_hash,canonical_payload,
           payload_hmac,hmac_key_version,consent_grant_id,consent_purpose,policy_version,workflow_version,
           lender_id,lender_version,offer_version,offer_amount_minor,term_months,offer_values,offer_expires_at,
           approval_expires_at,idempotency_key,created_at,created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (approval_id, borrower_id, workflow_id, result["snapshot_id"], result["input_hash"], canonical,
         digest, key_version, consent["id"], consent["purpose"], result["policy_version"], new_version,
         lender_id, result["result"]["lender_version"], result["result"]["offer_version"],
         offer["principal_minor"], offer["term_months"], Jsonb(offer), result["offer_expires_at"],
         approval_expiry, idempotency_key, now, actor),
    )
    conn.execute("INSERT INTO sensitive_approval_data(approval_id,canonical_payload,offer_amount_minor,term_months,offer_values) VALUES (%s,%s,%s,%s,%s)",
                 (approval_id, canonical, offer["principal_minor"], offer["term_months"], Jsonb(offer)))
    append_audit_event(conn, workflow_id, actor, "approval.created", "compared", "awaiting_approval",
                       {"approval_id": approval_id, "snapshot_id": str(result["snapshot_id"]),
                        "payload_hmac": digest, "lender_id": lender_id,
                        "offer_version": result["result"]["offer_version"],
                        "approval_expires_at": _iso(approval_expiry),
                        "offer_expires_at": _iso(result["offer_expires_at"])}, correlation_id)
    return {"approval_id": approval_id, "workflow_id": workflow_id, "lender_id": lender_id,
            "offer_values": offer, "offer_expires_at": result["offer_expires_at"],
            "approval_expires_at": approval_expiry, "workflow_version": new_version, "status": "pending"}


def _expire_or_invalidate(conn: Connection, approval: dict[str, Any], workflow: dict[str, Any],
                          status: str, reason: str, actor: str, correlation_id: str, now: datetime) -> dict[str, Any]:
    conn.execute("UPDATE approvals SET status=%s,invalidation_reason=%s WHERE id=%s",
                 (status, reason, approval["id"]))
    version = workflow["version"] + 1
    conn.execute("UPDATE workflows SET state='failed',version=%s,updated_at=%s WHERE id=%s",
                 (version, now, workflow["id"]))
    append_audit_event(conn, workflow["id"], actor, "approval.invalidated", workflow["state"], "failed",
                       {"approval_id": str(approval["id"]), "reason": reason}, correlation_id)
    return {"error": reason}


def consume_approval(conn: Connection, workflow_id: str, borrower_id: str, approval_id: str,
                     expected_workflow_version: int, actor: str, correlation_id: str,
                     clock: Clock = system_clock) -> dict[str, Any]:
    consent, workflow = lock_consent_then_workflow(conn, workflow_id, borrower_id)
    approval = conn.execute("SELECT * FROM approvals WHERE id=%s AND workflow_id=%s FOR UPDATE",
                            (approval_id, workflow_id)).fetchone()
    if not approval or approval["borrower_id"] != borrower_id:
        raise HTTPException(status_code=404, detail="Approval not found")
    if workflow["version"] != expected_workflow_version:
        raise HTTPException(status_code=409, detail="Workflow version is stale")
    if approval["status"] != "pending" or workflow["state"] != "awaiting_approval":
        raise HTTPException(status_code=409, detail="Approval is not pending")
    now = clock.now()
    if approval["approval_expires_at"] <= now or approval["offer_expires_at"] <= now:
        return _expire_or_invalidate(conn, approval, workflow, "expired", "approval_or_offer_expired_at_consumption",
                                     actor, correlation_id, now)
    if consent["status"] != "active" or str(consent["id"]) != str(approval["consent_grant_id"]):
        return _expire_or_invalidate(conn, approval, workflow, "invalidated", "consent_binding_changed",
                                     actor, correlation_id, now)
    snapshot = conn.execute("SELECT input_hash,policy_version FROM evaluation_snapshots WHERE id=%s AND workflow_id=%s",
                            (approval["snapshot_id"], workflow_id)).fetchone()
    current_offer = conn.execute(
        """SELECT sr.result,r.offer_expires_at FROM lender_results r
           JOIN sensitive_lender_result_data sr ON sr.lender_result_id=r.id
           WHERE r.workflow_id=%s AND r.snapshot_id=%s AND r.lender_id=%s""",
        (workflow_id, approval["snapshot_id"], approval["lender_id"]),
    ).fetchone()
    actual_digest, key_version = approval_hmac(_approval_payload_from_row(approval))
    binding_ok = bool(
        snapshot and snapshot["input_hash"] == approval["snapshot_hash"] and
        snapshot["policy_version"] == approval["policy_version"] == workflow["policy_version"] and
        current_offer and current_offer["result"].get("offer") == approval["offer_values"] and
        current_offer["result"].get("lender_version") == approval["lender_version"] and
        current_offer["result"].get("offer_version") == approval["offer_version"] and
        current_offer["offer_expires_at"] == approval["offer_expires_at"] and
        approval["workflow_version"] == workflow["version"] and
        approval["consent_purpose"] == consent["purpose"] and
        approval["hmac_key_version"] == key_version and
        hmac.compare_digest(actual_digest, approval["payload_hmac"]) and
        canonical_approval_bytes(_approval_payload_from_row(approval)).decode("ascii") == approval["canonical_payload"]
    )
    if not binding_ok:
        return _expire_or_invalidate(conn, approval, workflow, "invalidated", "approval_binding_changed",
                                     actor, correlation_id, now)
    consumed = conn.execute(
        "UPDATE approvals SET status='consumed',consumed_at=%s WHERE id=%s AND status='pending' RETURNING id",
        (now, approval_id),
    ).fetchone()
    if not consumed:
        raise HTTPException(status_code=409, detail="Approval was already consumed")
    new_version = workflow["version"] + 1
    conn.execute("UPDATE workflows SET state='submitting',version=%s,updated_at=%s WHERE id=%s",
                 (new_version, now, workflow_id))
    conn.execute(
        """INSERT INTO workflow_steps(workflow_id,approval_id,kind,payload,idempotency_key,available_at)
           VALUES (%s,%s,'submit_application',%s,%s,%s)""",
        (workflow_id, approval_id, Jsonb({"approval_id": str(approval_id)}),
         approval["idempotency_key"], now),
    )
    append_audit_event(conn, workflow_id, actor, "approval.consumed", "awaiting_approval", "submitting",
                       {"approval_id": str(approval_id), "workflow_version": new_version,
                        "idempotency_key": approval["idempotency_key"]}, correlation_id)
    return {"approval_id": str(approval_id), "workflow_id": workflow_id,
            "state": "submitting", "workflow_version": new_version, "status": "consumed"}
