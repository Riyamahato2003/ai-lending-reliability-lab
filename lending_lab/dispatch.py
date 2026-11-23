"""Write-ahead simulated submission and restart-safe reconciliation worker."""

import json
import re
from datetime import timedelta
from typing import Any, Callable

from fastapi import HTTPException
from psycopg import Connection
from psycopg.types.json import Jsonb

from lending_lab.approvals import _approval_payload_from_row, approval_hmac
from lending_lab.clock import Clock, system_clock
from lending_lab.db import append_audit_event, connect, run_transaction_with_retry, transaction
from lending_lab.policies import POLICY_VERSION, build_lender_payload, canonical_hash
from lending_lab.queue import claim_next_step, retry_step
from lending_lab.simulator import submit_simulated
from lending_lab.privacy import json_hmac

PROVIDER_DEADLINE_SECONDS = 10
RESULT_COMMIT_MARGIN_SECONDS = 5
DEFAULT_LEASE_SECONDS = 30


def validate_lease_budget(lease_seconds: int) -> None:
    if lease_seconds <= PROVIDER_DEADLINE_SECONDS + RESULT_COMMIT_MARGIN_SECONDS:
        raise ValueError("lease must exceed provider deadline plus result-commit margin")


def _valid_approval(conn: Connection, approval: dict[str, Any], workflow: dict[str, Any],
                    consent: dict[str, Any], *, require_active_consent: bool = True) -> bool:
    snapshot = conn.execute(
        """SELECT d.input_data,s.input_hash,s.policy_version FROM evaluation_snapshots s
           JOIN sensitive_evaluation_snapshot_data d ON d.snapshot_id=s.id
           WHERE s.id=%s AND s.workflow_id=%s""",
        (approval["snapshot_id"], workflow["id"]),
    ).fetchone()
    result = conn.execute(
        """SELECT d.result,r.offer_expires_at FROM lender_results r
           JOIN sensitive_lender_result_data d ON d.lender_result_id=r.id
           WHERE r.workflow_id=%s AND r.snapshot_id=%s AND r.lender_id=%s""",
        (workflow["id"], approval["snapshot_id"], approval["lender_id"]),
    ).fetchone()
    try:
        digest, key_version = approval_hmac(_approval_payload_from_row(approval))
    except (ValueError, UnicodeError):
        return False
    return bool(
        (not require_active_consent or consent["status"] == "active") and
        consent["id"] == approval["consent_grant_id"] and
        consent["purpose"] == approval["consent_purpose"] and snapshot and
        json_hmac("evaluation-input", snapshot["input_data"])[0] == snapshot["input_hash"] == approval["snapshot_hash"] and
        snapshot["policy_version"] == approval["policy_version"] == workflow["policy_version"] == POLICY_VERSION and
        result and result["result"].get("offer") == approval["offer_values"] and
        result["result"].get("lender_version") == approval["lender_version"] and
        result["result"].get("offer_version") == approval["offer_version"] and
        result["offer_expires_at"] == approval["offer_expires_at"] and
        approval["hmac_key_version"] == key_version and digest == approval["payload_hmac"]
    )


def _fail_before_effect(conn: Connection, workflow: dict[str, Any], approval: dict[str, Any],
                        step: dict[str, Any], reason: str, now, correlation_id: str) -> dict[str, Any]:
    conn.execute("UPDATE approvals SET status='invalidated',invalidation_reason=%s WHERE id=%s AND status='consumed'",
                 (reason, approval["id"]))
    version = workflow["version"] + 1
    conn.execute("UPDATE workflows SET state='failed',version=%s,updated_at=%s WHERE id=%s",
                 (version, now, workflow["id"]))
    conn.execute("UPDATE workflow_steps SET status='failed',result=%s,lease_token=NULL,lease_expires_at=NULL,updated_at=%s WHERE id=%s AND lease_token=%s",
                 (Jsonb({"reason": reason, "effect": "not_dispatched"}), now, step["id"], step["lease_token"]))
    append_audit_event(conn, workflow["id"], "dispatch-worker", "submission.blocked_before_intent",
                       workflow["state"], "failed", {"approval_id": str(approval["id"]), "reason": reason},
                       correlation_id)
    return {"status": "failed", "reason": reason}


def _prepare_dispatch(conn: Connection, step_id: str, lease_token: str, clock: Clock,
                      correlation_id: str) -> dict[str, Any]:
    locator = conn.execute(
        "SELECT s.workflow_id,s.approval_id,w.borrower_id,w.consent_grant_id FROM workflow_steps s "
        "JOIN workflows w ON w.id=s.workflow_id WHERE s.id=%s", (step_id,),
    ).fetchone()
    if not locator or not locator["approval_id"]:
        return {"status": "missing_step"}
    # Global order: consent, workflow, approval, then step.
    consent = conn.execute("SELECT * FROM lock_consent_grant(%s)",
                           (locator["consent_grant_id"],)).fetchone()
    workflow = conn.execute("SELECT * FROM workflows WHERE id=%s FOR UPDATE",
                            (locator["workflow_id"],)).fetchone()
    approval = conn.execute("SELECT * FROM approvals WHERE id=%s FOR UPDATE",
                            (locator["approval_id"],)).fetchone()
    step = conn.execute("SELECT * FROM workflow_steps WHERE id=%s FOR UPDATE", (step_id,)).fetchone()
    now = clock.now()
    if (not consent or not workflow or not approval or not step or step["status"] != "leased" or
            str(step["lease_token"]) != lease_token or not step["lease_expires_at"] or
            step["lease_expires_at"] <= now):
        return {"status": "stale_lease"}
    if workflow["state"] == "submitted":
        intent = conn.execute("SELECT * FROM submission_intents WHERE workflow_id=%s",
                              (workflow["id"],)).fetchone()
        return {"status": "already_submitted", "step_id": step_id, "intent": intent,
                "lease_token": lease_token}
    if workflow["state"] not in {"submitting", "dispatching", "submission_unknown"}:
        return {"status": "not_dispatchable", "workflow_state": workflow["state"]}
    if (approval["workflow_id"] != workflow["id"] or approval["borrower_id"] != workflow["borrower_id"] or
            approval["consent_grant_id"] != consent["id"] or approval["id"] != step["approval_id"]):
        return {"status": "binding_conflict"}
    intent = conn.execute("SELECT * FROM submission_intents WHERE workflow_id=%s",
                          (workflow["id"],)).fetchone()
    if intent and consent["status"] == "revoked":
        # Reconciliation is metadata-only: no snapshot, lender result, approval offer,
        # or borrower payload is needed to consult the original key and ledger digest.
        if intent["approval_id"] != approval["id"] or len(intent["payload_hash"]) != 64:
            return {"status": "binding_conflict", "intent_id": str(intent["id"])}
        return {"status": "existing_intent", "intent": intent, "can_send": False,
                "step_id": step_id, "lease_token": lease_token}
    # A committed intent may always be reconciled against its original key and
    # payload. Consent is checked only if this worker might create a new effect.
    if not _valid_approval(conn, approval, workflow, consent,
                           require_active_consent=intent is None):
        if workflow["state"] == "submitting":
            return _fail_before_effect(conn, workflow, approval, step, "approval_binding_changed", now, correlation_id)
        return {"status": "unresolved", "reason": "approval_binding_changed"}

    if intent:
        payload_row = conn.execute("SELECT lender_payload FROM sensitive_dispatch_payloads WHERE intent_id=%s",
                                   (intent["id"],)).fetchone()
        if intent["approval_id"] != approval["id"]:
            return {"status": "binding_conflict", "intent_id": str(intent["id"])}
        payload = dict(payload_row["lender_payload"]) if payload_row else None
        payload_matches = bool(payload and canonical_hash(payload) == intent["payload_hash"])
        can_send = (payload_matches and consent["status"] == "active" and approval["status"] == "consumed" and
                    approval["approval_expires_at"] > now and approval["offer_expires_at"] > now and
                    step["lease_expires_at"] > now + timedelta(seconds=PROVIDER_DEADLINE_SECONDS + RESULT_COMMIT_MARGIN_SECONDS))
        return {"status": "existing_intent", "intent": intent, "can_send": can_send,
                "payload": payload, "step_id": step_id, "lease_token": lease_token}

    if workflow["state"] != "submitting" or approval["status"] != "consumed":
        return {"status": "not_dispatchable", "workflow_state": workflow["state"]}
    if (approval["approval_expires_at"] <= now or approval["offer_expires_at"] <= now or
            approval["workflow_version"] + 1 != workflow["version"]):
        return _fail_before_effect(conn, workflow, approval, step,
                                   "approval_or_offer_expired_or_workflow_changed_at_dispatch", now, correlation_id)
    if step["lease_expires_at"] <= now + timedelta(seconds=PROVIDER_DEADLINE_SECONDS + RESULT_COMMIT_MARGIN_SECONDS):
        return {"status": "lease_budget_insufficient"}
    snapshot = conn.execute(
        """SELECT d.input_data,s.input_hash FROM evaluation_snapshots s
           JOIN sensitive_evaluation_snapshot_data d ON d.snapshot_id=s.id WHERE s.id=%s""",
                            (approval["snapshot_id"],)).fetchone()
    if not snapshot:
        return {"status": "unresolved", "reason": "sensitive_evaluation_payload_unavailable"}
    payload = build_lender_payload(approval["lender_id"], dict(snapshot["input_data"]))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    payload_hash = canonical_hash(payload)
    intent = conn.execute(
        """INSERT INTO submission_intents(workflow_id,approval_id,snapshot_id,lender_id,idempotency_key,
           payload_hash,approval_payload_hmac,created_at,correlation_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (workflow["id"], approval["id"], approval["snapshot_id"], approval["lender_id"],
         approval["idempotency_key"], payload_hash,
         approval["payload_hmac"], now, correlation_id),
    ).fetchone()
    conn.execute("INSERT INTO sensitive_dispatch_payloads(intent_id,lender_payload,canonical_payload) VALUES (%s,%s,%s)",
                 (intent["id"], Jsonb(payload), canonical))
    version = workflow["version"] + 1
    conn.execute("UPDATE workflows SET state='dispatching',version=%s,updated_at=%s WHERE id=%s",
                 (version, now, workflow["id"]))
    append_audit_event(conn, workflow["id"], "dispatch-worker", "submission.dispatch_intent_committed",
                       "submitting", "dispatching",
                       {"intent_id": str(intent["id"]), "approval_id": str(approval["id"]),
                        "lender_id": approval["lender_id"], "idempotency_key": approval["idempotency_key"],
                        "payload_hash": payload_hash, "snapshot_id": str(approval["snapshot_id"])}, correlation_id)
    return {"status": "new_intent", "intent": intent, "payload": payload, "can_send": True,
            "step_id": step_id, "lease_token": lease_token}


def _lookup_ledger(lender_id: str, key: str) -> dict[str, Any] | None:
    with connect() as conn:
        return conn.execute("SELECT payload_hash,response FROM simulator_ledger WHERE lender_id=%s AND idempotency_key=%s",
                            (lender_id, key)).fetchone()


def _persist_unknown(workflow_id: str, approval_id: str, step_id: str, lease_token: str,
                     reason: str, correlation_id: str, clock: Clock) -> None:
    def operation(conn):
        locator = conn.execute("SELECT consent_grant_id FROM workflows WHERE id=%s", (workflow_id,)).fetchone()
        if not locator:
            return
        consent = conn.execute("SELECT * FROM lock_consent_grant(%s)", (locator["consent_grant_id"],)).fetchone()
        workflow = conn.execute("SELECT * FROM workflows WHERE id=%s FOR UPDATE", (workflow_id,)).fetchone()
        conn.execute("SELECT id FROM approvals WHERE id=%s FOR UPDATE", (approval_id,))
        conn.execute("SELECT id FROM workflow_steps WHERE id=%s FOR UPDATE", (step_id,))
        if workflow["state"] == "dispatching":
            version = workflow["version"] + 1
            conn.execute("UPDATE workflows SET state='submission_unknown',version=%s,updated_at=%s WHERE id=%s",
                         (version, clock.now(), workflow_id))
            append_audit_event(conn, workflow_id, "dispatch-worker", "submission.outcome_unknown",
                               "dispatching", "submission_unknown", {"reason": reason}, correlation_id)
        elif workflow["state"] == "submission_unknown":
            append_audit_event(conn, workflow_id, "dispatch-worker", "submission.reconciliation_pending",
                               "submission_unknown", "submission_unknown", {"reason": reason}, correlation_id)
        if consent and consent["status"] == "revoked":
            conn.execute("""UPDATE workflow_steps SET status='dead_letter',dead_lettered_at=%s,lease_token=NULL,
                            lease_expires_at=NULL,updated_at=%s
                            WHERE id=%s AND status='leased' AND lease_token=%s""",
                         (clock.now(), clock.now(), step_id, lease_token))
        else:
            retry_step(conn, step_id, lease_token, clock)
    run_transaction_with_retry(operation)


def _record_conflict(intent: dict[str, Any], observed_hash: str, correlation_id: str,
                     clock: Clock) -> dict[str, Any]:
    def operation(conn):
        locator = conn.execute("SELECT consent_grant_id FROM workflows WHERE id=%s",
                               (intent["workflow_id"],)).fetchone()
        conn.execute("SELECT * FROM lock_consent_grant(%s)", (locator["consent_grant_id"],)).fetchone()
        workflow = conn.execute("SELECT * FROM workflows WHERE id=%s FOR UPDATE",
                                (intent["workflow_id"],)).fetchone()
        conn.execute("SELECT id FROM approvals WHERE id=%s FOR UPDATE", (intent["approval_id"],))
        step = conn.execute("SELECT * FROM workflow_steps WHERE workflow_id=%s AND approval_id=%s FOR UPDATE",
                            (intent["workflow_id"], intent["approval_id"])).fetchone()
        previous = workflow["state"]
        if previous == "dispatching":
            version = workflow["version"] + 1
            conn.execute("UPDATE workflows SET state='submission_unknown',version=%s,updated_at=%s WHERE id=%s",
                         (version, clock.now(), intent["workflow_id"]))
            next_state = "submission_unknown"
        else:
            next_state = previous
        append_audit_event(conn, intent["workflow_id"], "dispatch-worker", "submission.reconciliation_conflict",
                           previous, next_state,
                           {"intent_id": str(intent["id"]), "expected_payload_hash": intent["payload_hash"],
                            "observed_payload_hash": observed_hash}, correlation_id)
        if step and step["status"] == "leased":
            conn.execute("""UPDATE workflow_steps SET status='dead_letter',dead_lettered_at=%s,lease_token=NULL,
                          lease_expires_at=NULL,updated_at=%s WHERE id=%s""",
                         (clock.now(), clock.now(), step["id"]))
        return {"status": "conflict", "intent_id": str(intent["id"])}
    return run_transaction_with_retry(operation)


def _persist_confirmed(intent: dict[str, Any], response: dict[str, Any], step_id: str,
                       lease_token: str, correlation_id: str, clock: Clock,
                       during_transaction: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    def operation(conn):
        locator = conn.execute("SELECT consent_grant_id FROM workflows WHERE id=%s",
                               (intent["workflow_id"],)).fetchone()
        conn.execute("SELECT * FROM lock_consent_grant(%s)", (locator["consent_grant_id"],)).fetchone()
        workflow = conn.execute("SELECT * FROM workflows WHERE id=%s FOR UPDATE",
                                (intent["workflow_id"],)).fetchone()
        conn.execute("SELECT id FROM approvals WHERE id=%s FOR UPDATE", (intent["approval_id"],))
        step = conn.execute("SELECT * FROM workflow_steps WHERE id=%s FOR UPDATE", (step_id,)).fetchone()
        ledger = conn.execute(
            "SELECT payload_hash,response FROM simulator_ledger WHERE lender_id=%s AND idempotency_key=%s",
            (intent["lender_id"], intent["idempotency_key"]),
        ).fetchone()
        reason = _confirmation_error(intent, response, ledger)
        if reason:
            if workflow["state"] == "dispatching":
                previous = workflow["state"]
                version = workflow["version"] + 1
                conn.execute("UPDATE workflows SET state='submission_unknown',version=%s,updated_at=%s WHERE id=%s",
                             (version, clock.now(), intent["workflow_id"]))
                append_audit_event(conn, intent["workflow_id"], "dispatch-worker",
                                   "submission.outcome_unverified", previous, "submission_unknown",
                                   {"intent_id": str(intent["id"]), "reason": reason}, correlation_id)
            if (step and step["status"] == "leased" and str(step["lease_token"]) == lease_token and
                    step["lease_expires_at"] > clock.now()):
                retry_step(conn, step_id, lease_token, clock)
            return {"status": "submission_unknown", "workflow_state": "submission_unknown",
                    "reason": reason}
        if workflow["state"] in {"dispatching", "submission_unknown"}:
            previous = workflow["state"]
            version = workflow["version"] + 1
            conn.execute("UPDATE workflows SET state='submitted',version=%s,updated_at=%s WHERE id=%s",
                         (version, clock.now(), intent["workflow_id"]))
            append_audit_event(conn, intent["workflow_id"], "dispatch-worker", "submission.confirmed",
                               previous, "submitted",
                               {"intent_id": str(intent["id"]),
                                "simulated_application_id": response["simulated_application_id"],
                                "lender_id": intent["lender_id"], "idempotency_key": intent["idempotency_key"],
                                "payload_hash": intent["payload_hash"]}, correlation_id)
        if (step and step["status"] == "leased" and str(step["lease_token"]) == lease_token and
                step["lease_expires_at"] > clock.now()):
            conn.execute("""UPDATE workflow_steps SET status='done',result=%s,lease_token=NULL,
                          lease_expires_at=NULL,updated_at=%s WHERE id=%s""",
                         (Jsonb(response), clock.now(), step_id))
        elif step and step["status"] in {"dead_letter", "failed", "queued"}:
            # The matching simulator ledger proves the effect. Completing a stopped
            # queue record cannot authorize another send and preserves revocation fencing.
            conn.execute("""UPDATE workflow_steps SET status='done',result=%s,lease_token=NULL,
                          lease_expires_at=NULL,updated_at=%s WHERE id=%s""",
                         (Jsonb(response), clock.now(), step_id))
        elif step and step["status"] != "done":
            return {"status": "confirmed_by_stale_worker", "workflow_state": workflow["state"]}
        if during_transaction:
            during_transaction(response)
        return {"status": "submitted", "workflow_state": "submitted", "response": response}
    return run_transaction_with_retry(operation)


def _confirmation_error(intent: dict[str, Any], response: Any, ledger: dict[str, Any] | None) -> str | None:
    """Validate a response against both immutable intent and authoritative simulator ledger."""
    if not isinstance(response, dict):
        return "response_not_object"
    expected = {"simulated_application_id", "status", "lender_id", "payload_hash", "is_real_submission"}
    if set(response) != expected:
        return "response_schema_mismatch"
    if (not isinstance(response["simulated_application_id"], str) or
            not re.fullmatch(r"[0-9a-f]{20}", response["simulated_application_id"])):
        return "invalid_simulated_application_id"
    expected_application_id = canonical_hash({"lender": intent["lender_id"],
                                               "key": intent["idempotency_key"]})[:20]
    if response["simulated_application_id"] != expected_application_id:
        return "simulated_application_id_mismatch"
    if response["status"] != "accepted_for_simulation" or response["is_real_submission"] is not False:
        return "unsupported_response_status"
    if not isinstance(response["lender_id"], str) or response["lender_id"] != intent["lender_id"]:
        return "response_lender_mismatch"
    if not isinstance(response["payload_hash"], str) or response["payload_hash"] != intent["payload_hash"]:
        return "response_payload_hash_mismatch"
    if not ledger:
        return "simulator_ledger_confirmation_missing"
    if ledger["payload_hash"] != intent["payload_hash"]:
        return "simulator_ledger_payload_conflict"
    if ledger["response"] != response:
        return "response_differs_from_authoritative_ledger"
    return None


def _allow_send(intent_id: str, step_id: str, lease_token: str, clock: Clock) -> bool:
    def operation(conn):
        intent = conn.execute("SELECT * FROM submission_intents WHERE id=%s", (intent_id,)).fetchone()
        if not intent:
            return False
        locator = conn.execute("SELECT consent_grant_id FROM workflows WHERE id=%s",
                               (intent["workflow_id"],)).fetchone()
        consent = conn.execute("SELECT * FROM lock_consent_grant(%s)",
                               (locator["consent_grant_id"],)).fetchone()
        workflow = conn.execute("SELECT * FROM workflows WHERE id=%s FOR UPDATE",
                                (intent["workflow_id"],)).fetchone()
        approval = conn.execute("SELECT * FROM approvals WHERE id=%s FOR UPDATE",
                                (intent["approval_id"],)).fetchone()
        step = conn.execute("SELECT * FROM workflow_steps WHERE id=%s FOR UPDATE", (step_id,)).fetchone()
        now = clock.now()
        valid = _valid_approval(conn, approval, workflow, consent)
        return bool(
            valid and consent["status"] == "active" and workflow["state"] in {"dispatching", "submission_unknown"} and
            approval["status"] == "consumed" and approval["approval_expires_at"] > now and
            approval["offer_expires_at"] > now and step and step["status"] == "leased" and
            str(step["lease_token"]) == lease_token and step["lease_expires_at"] > now +
            timedelta(seconds=PROVIDER_DEADLINE_SECONDS + RESULT_COMMIT_MARGIN_SECONDS)
        )
    return run_transaction_with_retry(operation)


def _send_simulated(lender_id: str, idempotency_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    # This is an independent transaction for the simulated lender ledger. The dispatch
    # intent transaction has already committed before this connection is opened.
    with transaction() as conn:
        return submit_simulated(conn, lender_id, idempotency_key, payload)


def process_leased_step(step: dict[str, Any], *, clock: Clock = system_clock,
                        sender: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
                        after_intent_commit: Callable[[dict[str, Any]], None] | None = None,
                        after_lender_effect: Callable[[dict[str, Any]], None] | None = None,
                        before_lender_send: Callable[[dict[str, Any]], None] | None = None,
                        before_result_persist: Callable[[dict[str, Any]], None] | None = None,
                        during_result_persist: Callable[[dict[str, Any]], None] | None = None,
                        correlation_id: str = "dispatch-worker") -> dict[str, Any]:
    """Commit intent first, then send, then record outcome in a separate DB transaction."""
    validate_lease_budget(DEFAULT_LEASE_SECONDS)
    step_id, lease_token = str(step["id"]), str(step["lease_token"])
    prepared = run_transaction_with_retry(
        lambda conn: _prepare_dispatch(conn, step_id, lease_token, clock, correlation_id))
    if prepared["status"] == "already_submitted":
        intent = prepared.get("intent")
        if intent:
            ledger = _lookup_ledger(intent["lender_id"], intent["idempotency_key"])
            if ledger and ledger["payload_hash"] == intent["payload_hash"]:
                return _persist_confirmed(intent, ledger["response"], step_id, lease_token,
                                          correlation_id, clock, during_result_persist)
        return prepared
    if prepared["status"] not in {"new_intent", "existing_intent"}:
        return prepared
    intent = prepared["intent"]
    dispatch_payload = prepared.get("payload")
    if after_intent_commit:
        after_intent_commit(intent)

    # Recovery always consults the lender ledger before replay. For a newly-created
    # intent it is also safe to check: the key/payload are already durable.
    ledger = _lookup_ledger(intent["lender_id"], intent["idempotency_key"])
    if ledger:
        if ledger["payload_hash"] != intent["payload_hash"]:
            return _record_conflict(intent, ledger["payload_hash"], correlation_id, clock)
        return _persist_confirmed(intent, ledger["response"], step_id, lease_token, correlation_id,
                                  clock, during_result_persist)
    if before_lender_send:
        before_lender_send({**intent, "lender_payload": dispatch_payload})
    if (not prepared["can_send"] or dispatch_payload is None or
            not _allow_send(str(intent["id"]), step_id, lease_token, clock)):
        _persist_unknown(intent["workflow_id"], intent["approval_id"], step_id, lease_token,
                         "send_blocked_after_intent_or_lease_expired", correlation_id, clock)
        return {"status": "unresolved", "intent_id": str(intent["id"]),
                "reason": "send_not_authorized_or_lease_stale"}
    send = sender or _send_simulated
    try:
        response = send(intent["lender_id"], intent["idempotency_key"], dispatch_payload)
        if after_lender_effect:
            after_lender_effect(response)
        if before_result_persist:
            before_result_persist(response)
    except HTTPException as exc:
        if exc.status_code == 409:
            ledger = _lookup_ledger(intent["lender_id"], intent["idempotency_key"])
            if ledger:
                return _record_conflict(intent, ledger["payload_hash"], correlation_id, clock)
        _persist_unknown(intent["workflow_id"], intent["approval_id"], step_id, lease_token,
                         f"simulated_provider_uncertain_{exc.status_code}", correlation_id, clock)
        return {"status": "submission_unknown", "intent_id": str(intent["id"])}
    except Exception:
        # The effect may have committed before the exception; preserve uncertainty.
        _persist_unknown(intent["workflow_id"], intent["approval_id"], step_id, lease_token,
                         "provider_response_unavailable", correlation_id, clock)
        return {"status": "submission_unknown", "intent_id": str(intent["id"])}
    # Treat a malformed or contradictory transport response as uncertainty. The
    # next leased worker will independently reconcile from the simulator ledger.
    ledger = _lookup_ledger(intent["lender_id"], intent["idempotency_key"])
    invalid_response = _confirmation_error(intent, response, ledger)
    if invalid_response:
        _persist_unknown(intent["workflow_id"], intent["approval_id"], step_id, lease_token,
                         invalid_response, correlation_id, clock)
        return {"status": "submission_unknown", "intent_id": str(intent["id"]),
                "reason": invalid_response}
    return _persist_confirmed(intent, response, step_id, lease_token, correlation_id,
                              clock, during_result_persist)


def run_dispatch_worker_once(*, clock: Clock = system_clock, lease_seconds: int = DEFAULT_LEASE_SECONDS,
                             sender=None, after_intent_commit=None, after_lender_effect=None,
                             before_lender_send=None, before_result_persist=None, during_result_persist=None,
                             correlation_id: str = "dispatch-worker") -> dict[str, Any]:
    validate_lease_budget(lease_seconds)
    step = run_transaction_with_retry(lambda conn: claim_next_step(conn, lease_seconds, clock,
                                                                    kind="submit_application"))
    if not step:
        return {"status": "idle"}
    return process_leased_step(step, clock=clock, sender=sender,
                               after_intent_commit=after_intent_commit,
                               after_lender_effect=after_lender_effect,
                               before_lender_send=before_lender_send,
                               before_result_persist=before_result_persist,
                               during_result_persist=during_result_persist,
                               correlation_id=correlation_id)


def recover_evaluating_workflows(clock: Clock = system_clock, limit: int = 100) -> int:
    """Resume only committed immutable evaluations; fail closed without a source snapshot."""
    rows = run_transaction_with_retry(lambda conn: conn.execute(
        "SELECT id,borrower_id FROM workflows WHERE state='evaluating' ORDER BY id LIMIT %s",
        (limit,),
    ).fetchall())
    recovered = 0
    for locator in rows:
        def operation(conn):
            wf_locator = conn.execute("SELECT consent_grant_id FROM workflows WHERE id=%s AND borrower_id=%s",
                                      (locator["id"], locator["borrower_id"])).fetchone()
            if not wf_locator:
                return False
            consent = conn.execute("SELECT * FROM lock_consent_grant(%s)",
                                   (wf_locator["consent_grant_id"],)).fetchone()
            workflow = conn.execute("SELECT * FROM workflows WHERE id=%s AND borrower_id=%s FOR UPDATE",
                                    (locator["id"], locator["borrower_id"])).fetchone()
            if not workflow or workflow["state"] != "evaluating":
                return False
            snapshot = conn.execute(
                                    """SELECT s.*,d.input_data FROM evaluation_snapshots s
                                       JOIN sensitive_evaluation_snapshot_data d ON d.snapshot_id=s.id
                                       WHERE s.workflow_id=%s ORDER BY s.created_at DESC LIMIT 1""",
                                    (workflow["id"],)).fetchone()
            if not snapshot or snapshot["policy_version"] != POLICY_VERSION or consent["status"] != "active":
                state, reason = "failed", "evaluation_snapshot_or_policy_unavailable"
                version = workflow["version"] + 1
                conn.execute("UPDATE workflows SET state=%s,version=%s,updated_at=%s WHERE id=%s",
                             (state, version, clock.now(), workflow["id"]))
                append_audit_event(conn, workflow["id"], "recovery-worker", "workflow.evaluation_recovery_failed",
                                   "evaluating", state, {"reason": reason}, "evaluation-recovery")
                return True
            from lending_lab.policies import LENDERS, compare_results, evaluate
            outcomes = compare_results([evaluate(lender, snapshot["input_data"]) for lender in LENDERS])
            for outcome in outcomes:
                result_row = conn.execute("""INSERT INTO lender_results(workflow_id,snapshot_id,lender_id,offer_expires_at)
                    VALUES (%s,%s,%s,%s) ON CONFLICT(workflow_id,lender_id,snapshot_id) DO NOTHING RETURNING id""",
                    (workflow["id"], snapshot["id"], outcome["lender_id"],
                     clock.now() + timedelta(minutes=30) if outcome.get("offer") else None)).fetchone()
                if result_row:
                    conn.execute("INSERT INTO sensitive_lender_result_data(lender_result_id,result) VALUES (%s,%s)",
                                 (result_row["id"], Jsonb(outcome)))
            state = "compared" if any(item["eligible_under_demo_rules"] for item in outcomes) else "ineligible"
            version = workflow["version"] + 1
            conn.execute("UPDATE workflows SET state=%s,version=%s,updated_at=%s WHERE id=%s",
                         (state, version, clock.now(), workflow["id"]))
            append_audit_event(conn, workflow["id"], "recovery-worker", "workflow.evaluation_recovered",
                               "evaluating", state,
                               {"snapshot_id": str(snapshot["id"]), "input_hash": snapshot["input_hash"],
                                "policy_version": snapshot["policy_version"]}, "evaluation-recovery")
            return True
        recovered += int(run_transaction_with_retry(operation))
    return recovered


def recover_interrupted_workflows(*, clock: Clock = system_clock, limit: int = 100) -> dict[str, int]:
    evaluations = recover_evaluating_workflows(clock, limit)
    submissions = 0
    for _ in range(limit):
        result = run_dispatch_worker_once(clock=clock)
        if result["status"] == "idle":
            break
        submissions += 1
    revoked = recover_revoked_intents(clock=clock, limit=limit)
    return {"evaluations_recovered": evaluations, "submission_steps_processed": submissions,
            "revoked_intents_reconciled": revoked}


def recover_revoked_intents(*, clock: Clock = system_clock, limit: int = 100) -> int:
    """Reconcile revoked dispatches using only immutable intent metadata and ledger rows."""
    rows = run_transaction_with_retry(lambda conn: conn.execute(
        """SELECT i.id,i.workflow_id,i.approval_id,i.lender_id,i.idempotency_key,i.payload_hash
           FROM submission_intents i JOIN workflows w ON w.id=i.workflow_id
           JOIN consent_grants c ON c.id=w.consent_grant_id
           WHERE c.status='revoked' AND w.state IN ('dispatching','submission_unknown')
           ORDER BY i.created_at,i.id LIMIT %s""", (limit,),
    ).fetchall())
    reconciled = 0
    for intent in rows:
        ledger = _lookup_ledger(intent["lender_id"], intent["idempotency_key"])
        if not ledger:
            continue
        if ledger["payload_hash"] != intent["payload_hash"]:
            _record_conflict(intent, ledger["payload_hash"], "revoked-reconciliation", clock)
            continue
        # Acquire the normal consent -> workflow -> approval -> step order and trust
        # only the ledger response, never deleted borrower or adapter payload data.
        def operation(conn):
            locator = conn.execute("SELECT consent_grant_id FROM workflows WHERE id=%s",
                                   (intent["workflow_id"],)).fetchone()
            consent = conn.execute("SELECT * FROM lock_consent_grant(%s)",
                                   (locator["consent_grant_id"],)).fetchone()
            workflow = conn.execute("SELECT * FROM workflows WHERE id=%s FOR UPDATE",
                                    (intent["workflow_id"],)).fetchone()
            conn.execute("SELECT id FROM approvals WHERE id=%s FOR UPDATE", (intent["approval_id"],))
            step = conn.execute("SELECT * FROM workflow_steps WHERE workflow_id=%s AND approval_id=%s FOR UPDATE",
                                (intent["workflow_id"], intent["approval_id"])).fetchone()
            fresh = conn.execute("SELECT payload_hash,response FROM simulator_ledger WHERE lender_id=%s AND idempotency_key=%s",
                                 (intent["lender_id"], intent["idempotency_key"])).fetchone()
            if consent["status"] != "revoked" or not fresh or fresh["payload_hash"] != intent["payload_hash"]:
                return False
            error = _confirmation_error(intent, fresh["response"], fresh)
            if error:
                append_audit_event(conn, workflow["id"], "recovery-worker",
                                   "submission.revoked_reconciliation_unverified",
                                   workflow["state"], workflow["state"],
                                   {"intent_id": str(intent["id"]), "reason": error},
                                   "revoked-reconciliation")
                return False
            previous = workflow["state"]
            conn.execute("UPDATE workflows SET state='submitted',version=version+1,updated_at=%s WHERE id=%s",
                         (clock.now(), workflow["id"]))
            append_audit_event(conn, workflow["id"], "recovery-worker", "submission.confirmed",
                               previous, "submitted",
                               {"intent_id": str(intent["id"]),
                                "simulated_application_id": fresh["response"]["simulated_application_id"],
                                "lender_id": intent["lender_id"], "idempotency_key": intent["idempotency_key"],
                                "payload_hash": intent["payload_hash"], "consent_revoked": True},
                               "revoked-reconciliation")
            if step and step["status"] != "done":
                conn.execute("""UPDATE workflow_steps SET status='done',result=%s,lease_token=NULL,
                              lease_expires_at=NULL,updated_at=%s WHERE id=%s""",
                             (Jsonb(fresh["response"]), clock.now(), step["id"]))
            return True
        reconciled += int(run_transaction_with_retry(operation))
    return reconciled


def list_reconciliation_conflicts(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT i.id AS intent_id,i.workflow_id,i.approval_id,i.lender_id,i.idempotency_key,
                  i.payload_hash AS expected_payload_hash,l.payload_hash AS observed_payload_hash,
                  w.state,w.updated_at
           FROM submission_intents i JOIN workflows w ON w.id=i.workflow_id
           JOIN simulator_ledger l ON l.lender_id=i.lender_id AND l.idempotency_key=i.idempotency_key
           WHERE l.payload_hash<>i.payload_hash ORDER BY i.created_at,i.id"""
    ).fetchall()
    return [{**row, "intent_id": str(row["intent_id"]), "workflow_id": str(row["workflow_id"]),
             "approval_id": str(row["approval_id"])} for row in rows]


def record_operator_resolution(conn: Connection, intent_id: str, operator_username: str,
                               disposition: str, evidence: str, correlation_id: str,
                               clock: Clock = system_clock) -> dict[str, Any]:
    conflict = conn.execute(
        """SELECT i.id,i.workflow_id,i.payload_hash,l.payload_hash AS observed_hash
           FROM submission_intents i JOIN simulator_ledger l
             ON l.lender_id=i.lender_id AND l.idempotency_key=i.idempotency_key
           WHERE i.id=%s AND l.payload_hash<>i.payload_hash""", (intent_id,),
    ).fetchone()
    if not conflict:
        raise HTTPException(status_code=404, detail="Unresolved idempotency conflict not found")
    event = conn.execute(
        """INSERT INTO operator_resolution_events(intent_id,operator_username,disposition,evidence,created_at)
           VALUES (%s,%s,%s,%s,%s) RETURNING id""",
        (intent_id, operator_username, disposition, evidence, clock.now()),
    ).fetchone()
    append_audit_event(conn, conflict["workflow_id"], operator_username,
                       "operator.reconciliation_review_recorded", "submission_unknown", "submission_unknown",
                       {"intent_id": str(intent_id), "review_id": str(event["id"]),
                        "disposition": disposition, "expected_payload_hash": conflict["payload_hash"],
                        "observed_payload_hash": conflict["observed_hash"]}, correlation_id)
    return {"review_id": str(event["id"]), "intent_id": str(intent_id), "status": "recorded_unresolved"}
