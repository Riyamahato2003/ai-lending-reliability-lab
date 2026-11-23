"""Deterministic, operator-only experiments isolated in lab-role-owned tables.

This module never connects through the borrower/runtime schema for experiment
effects. Safe and unsafe implementations share the same fault schedule; the
unsafe implementation is a teaching model, not a mutation of application code.
"""

from __future__ import annotations

import hashlib
import json
import platform
import random
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from lending_lab.db import connect_lab
from lending_lab.dispatch import _confirmation_error
from lending_lab.policies import canonical_hash
from lending_lab.schemas import LabFaultScenario, LabImplementation, LabRunRequest

EXPERIMENT_VERSION = "3c-v1"
LAB_LENDER_ID = "lab-simulator"
PROVIDER_DEADLINE_MS = 100
LEASE_BOUNDARY_MS = 150
INVARIANTS = (
    "no_unauthorized_lender_effect",
    "no_duplicate_lender_effect_for_application",
    "no_effect_after_revocation_commit",
    "no_confirmation_without_authoritative_evidence",
    "immutable_approval_and_dispatch_bindings",
    "uncertain_outcome_not_reported_as_rejection",
    "lab_database_isolation",
)

SCENARIOS: dict[LabFaultScenario, dict[str, str]] = {
    LabFaultScenario.REQUEST_LOST_BEFORE_LENDER: {
        "label": "REQUEST_LOST_BEFORE_LENDER",
        "description": "The first provider request never reaches the simulated lender; no effect is committed.",
    },
    LabFaultScenario.EFFECT_COMMITTED_RESPONSE_LOST: {
        "label": "EFFECT_COMMITTED_RESPONSE_LOST",
        "description": "The simulated lender commits an effect, then the response is lost.",
    },
    LabFaultScenario.DUPLICATE_RESPONSE: {
        "label": "DUPLICATE_RESPONSE",
        "description": "The transport delivers the same confirmation twice for one provider attempt.",
    },
    LabFaultScenario.CONFLICTING_RESPONSE: {
        "label": "CONFLICTING_RESPONSE",
        "description": "A response contradicts the immutable dispatch intent and simulator ledger.",
    },
    LabFaultScenario.RESPONSE_AFTER_DEADLINE: {
        "label": "RESPONSE_AFTER_DEADLINE",
        "description": "A response arrives beyond both the configured provider deadline and lease boundary.",
    },
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def _commit_identity() -> str:
    repo = Path(__file__).resolve().parent.parent
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                             capture_output=True, text=True, timeout=2).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True,
                               capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return sha if not dirty else f"uncommitted:{sha}"


def _effect_response(lender_id: str, key: str, payload_hash: str) -> dict[str, Any]:
    return {
        "simulated_application_id": canonical_hash({"lender": lender_id, "key": key})[:20],
        "status": "accepted_for_simulation",
        "lender_id": lender_id,
        "payload_hash": payload_hash,
        "is_real_submission": False,
    }


def _safe_send(conn: Connection, run_id: str, trial: int, key: str, payload_hash: str,
               *, consent_active: bool, logical_step: int) -> dict[str, Any] | None:
    """Lab-local equivalent of the simulator ledger's same-key/same-payload rule."""
    if not consent_active:
        return None
    intent = conn.execute(
        """SELECT lender_id,idempotency_key,payload_hash FROM lab_dispatch_intents
           WHERE run_id=%s AND trial_index=%s""", (run_id, trial),
    ).fetchone()
    if not intent or (intent["lender_id"], intent["idempotency_key"], intent["payload_hash"]) != \
            (LAB_LENDER_ID, key, payload_hash):
        raise ValueError("safe lab send lacks its committed matching dispatch intent")
    existing = conn.execute(
        """SELECT payload_hash,response FROM lab_simulator_ledger
           WHERE run_id=%s AND trial_index=%s AND lender_id=%s AND idempotency_key=%s""",
        (run_id, trial, LAB_LENDER_ID, key),
    ).fetchone()
    if existing:
        if existing["payload_hash"] != payload_hash:
            raise ValueError("lab idempotency key reused with a different payload")
        return existing["response"]
    response = _effect_response(LAB_LENDER_ID, key, payload_hash)
    conn.execute(
        """INSERT INTO lab_simulator_ledger(run_id,trial_index,lender_id,idempotency_key,payload_hash,response)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (run_id, trial, LAB_LENDER_ID, key, payload_hash, Jsonb(response)),
    )
    prior = conn.execute("""SELECT count(*) AS n FROM lab_effects
                           WHERE run_id=%s AND trial_index=%s""", (run_id, trial)).fetchone()["n"]
    conn.execute(
        """INSERT INTO lab_effects(run_id,trial_index,effect_index,lender_id,idempotency_key,
           payload_hash,authorized_at_effect,logical_step) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (run_id, trial, prior + 1, LAB_LENDER_ID, key, payload_hash, consent_active, logical_step),
    )
    return response


def _unsafe_send(conn: Connection, run_id: str, trial: int, key: str, payload_hash: str,
                 *, consent_active: bool, logical_step: int) -> dict[str, Any]:
    """Intentionally omits ledger uniqueness, consent recheck, and reconciliation."""
    prior = conn.execute("""SELECT count(*) AS n FROM lab_effects
                           WHERE run_id=%s AND trial_index=%s""", (run_id, trial)).fetchone()["n"]
    conn.execute(
        """INSERT INTO lab_effects(run_id,trial_index,effect_index,lender_id,idempotency_key,
           payload_hash,authorized_at_effect,logical_step) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (run_id, trial, prior + 1, LAB_LENDER_ID, key, payload_hash, consent_active, logical_step),
    )
    return _effect_response(LAB_LENDER_ID, key, payload_hash)


def _authoritative_response(conn: Connection, run_id: str, trial: int, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT payload_hash,response FROM lab_simulator_ledger
           WHERE run_id=%s AND trial_index=%s AND lender_id=%s AND idempotency_key=%s""",
        (run_id, trial, LAB_LENDER_ID, key),
    ).fetchone()
    return row


def _lab_isolation_violation(conn: Connection) -> int:
    """Check the actual connected role has no access to borrower/submission data."""
    protected = (
        "borrower_profiles", "sensitive_borrower_data", "consent_grants",
        "evaluation_snapshots", "sensitive_evaluation_snapshot_data", "lender_results",
        "sensitive_lender_result_data", "workflows", "workflow_steps",
        "approvals", "sensitive_approval_data", "submission_intents",
        "sensitive_dispatch_payloads", "simulator_ledger",
    )
    for table in protected:
        row = conn.execute(
            "SELECT has_table_privilege(current_user,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS allowed",
            (table,),
        ).fetchone()
        if row["allowed"]:
            return 1
    if conn.execute("SELECT current_user = 'lending_lab_operator' AS is_lab_role").fetchone()["is_lab_role"] is not True:
        return 1
    return 0


def _trial(conn: Connection, run_id: str, trial: int, request: LabRunRequest,
           key: str) -> dict[str, Any]:
    payload_hash = canonical_hash({"experiment_version": EXPERIMENT_VERSION,
                                   "seed": request.seed, "trial": trial})
    intent = {"lender_id": LAB_LENDER_ID, "idempotency_key": key, "payload_hash": payload_hash}
    safe = request.implementation == LabImplementation.SAFE
    send = _safe_send if safe else _unsafe_send
    state = "dispatching"
    consent_active = True
    revocation_step: int | None = None
    binding_mutations = 0
    uncertainty_collapses = 0
    unverified_accepted = 0
    rejected_unverified = 0
    duplicate_responses = 0
    late_responses = 0
    recovered = False
    recovery_steps = 0
    recovery_duration_ms = 0.0
    confirmed_authoritatively = False
    trace = ["approval.authorized_in_synthetic_lab", "dispatch.intent_committed_in_lab"]
    provider_attempts = 0
    logical_step = 1

    if request.scenario == LabFaultScenario.REQUEST_LOST_BEFORE_LENDER:
        provider_attempts += 1
        state = "submission_unknown"
        trace.append("fault.request_lost_before_lender; no_effect")
        if not safe:
            state = "rejected"
            uncertainty_collapses = 1
            trace.append("unsafe.timeout_misreported_as_rejection")
    else:
        provider_attempts += 1
        first_response = send(conn, run_id, trial, key, payload_hash,
                              consent_active=consent_active, logical_step=logical_step)
        if request.scenario == LabFaultScenario.EFFECT_COMMITTED_RESPONSE_LOST:
            state = "submission_unknown"
            trace.append("fault.response_lost_after_effect_commit")
        elif request.scenario == LabFaultScenario.DUPLICATE_RESPONSE:
            duplicate_responses = 1
            if safe:
                ledger = _authoritative_response(conn, run_id, trial, key)
                valid_first = _confirmation_error(intent, first_response, ledger) is None
                valid_duplicate = _confirmation_error(intent, first_response, ledger) is None
                if valid_first and valid_duplicate:
                    state, confirmed_authoritatively = "submitted", True
                    trace.extend(["confirmation.ledger_verified", "fault.duplicate_response_ignored"])
                else:
                    state = "submission_unknown"
            else:
                # The model processes the duplicate twice, but a single request had
                # already committed one effect; it does not invent a second send.
                state = "submitted"
                unverified_accepted = 1
                trace.append("unsafe.duplicate_confirmation_applied_without_ledger")
        elif request.scenario == LabFaultScenario.CONFLICTING_RESPONSE:
            bad_response = {**first_response, "payload_hash": "f" * 64}
            if safe:
                if _confirmation_error(intent, bad_response,
                                       _authoritative_response(conn, run_id, trial, key)):
                    rejected_unverified = 1
                    state = "submission_unknown"
                    trace.append("response.conflict_rejected; state_unknown")
            else:
                # Explicitly unsafe: mutate the stored binding to fit the bad response.
                intent["payload_hash"] = bad_response["payload_hash"]
                binding_mutations = 1
                unverified_accepted = 1
                state = "submitted"
                trace.append("unsafe.intent_digest_mutated; conflicting_response_trusted")
        elif request.scenario == LabFaultScenario.RESPONSE_AFTER_DEADLINE:
            late_responses = 1
            if safe:
                state = "submission_unknown"
                rejected_unverified = 1
                trace.append(f"fault.response_arrived_after_{LEASE_BOUNDARY_MS}ms_lease_boundary")
            else:
                state = "submitted"
                unverified_accepted = 1
                trace.append("unsafe.late_response_accepted_after_lease")

    if request.revoke_before_recovery:
        # For effect-committed scenarios this is explicitly after the effect.
        consent_active = False
        logical_step = 2
        revocation_step = logical_step
        trace.append("consent.revocation_committed_in_lab")

    if state in {"submission_unknown", "rejected"}:
        recovery_started = time.perf_counter_ns()
        recovery_steps += 1
        logical_step += 1
        if request.scenario == LabFaultScenario.REQUEST_LOST_BEFORE_LENDER:
            if request.revoke_before_recovery and safe:
                trace.append("recovery.retry_blocked_by_revoked_grant; no_effect")
            else:
                provider_attempts += 1
                retry_response = send(conn, run_id, trial, key, payload_hash,
                                      consent_active=consent_active, logical_step=logical_step)
                if retry_response:
                    if safe:
                        ledger = _authoritative_response(conn, run_id, trial, key)
                        if _confirmation_error(intent, retry_response, ledger) is None:
                            state, confirmed_authoritatively, recovered = "submitted", True, True
                            trace.append("recovery.same_key_retry_ledger_verified")
                    else:
                        state, unverified_accepted = "submitted", 1
                        trace.append("unsafe.blind_retry_response_trusted")
        elif safe:
            ledger = _authoritative_response(conn, run_id, trial, key)
            if ledger and ledger["payload_hash"] == payload_hash and \
                    _confirmation_error(intent, ledger["response"], ledger) is None:
                state, confirmed_authoritatively, recovered = "submitted", True, True
                trace.append("recovery.original_key_authoritative_ledger_hit")
            else:
                trace.append("recovery.no_authoritative_effect; remains_unknown")
        else:
            # The deliberately unsafe recovery blindly repeats even when consent
            # was withdrawn or a prior effect already committed.
            provider_attempts += 1
            unsafe_response = send(conn, run_id, trial, key, payload_hash,
                                   consent_active=consent_active, logical_step=logical_step)
            state, unverified_accepted = "submitted", int(unsafe_response is not None)
            trace.append("unsafe.blind_retry_without_ledger_or_consent_check")
        recovery_duration_ms = (time.perf_counter_ns() - recovery_started) / 1_000_000

    ledger = _authoritative_response(conn, run_id, trial, key)
    effect_rows = conn.execute(
        "SELECT authorized_at_effect,logical_step FROM lab_effects WHERE run_id=%s AND trial_index=%s ORDER BY effect_index",
        (run_id, trial),
    ).fetchall()
    actual_effects = len(effect_rows)
    duplicates = max(0, actual_effects - 1)
    unauthorized = sum(not row["authorized_at_effect"] for row in effect_rows)
    after_revocation = sum(revocation_step is not None and row["logical_step"] > revocation_step
                           for row in effect_rows)
    violations = {name: 0 for name in INVARIANTS}
    violations["no_unauthorized_lender_effect"] = unauthorized
    violations["no_duplicate_lender_effect_for_application"] = duplicates
    violations["no_effect_after_revocation_commit"] = after_revocation
    violations["no_confirmation_without_authoritative_evidence"] = unverified_accepted
    violations["immutable_approval_and_dispatch_bindings"] = binding_mutations
    violations["uncertain_outcome_not_reported_as_rejection"] = uncertainty_collapses
    violations["lab_database_isolation"] = _lab_isolation_violation(conn)
    return {
        "trial_index": trial,
        "final_state": state,
        "provider_attempts": provider_attempts,
        "effects_created": actual_effects,
        "confirmed_effects": int(confirmed_authoritatively),
        "workflows_marked_submitted": int(state == "submitted"),
        "authoritative_ledger_present": bool(ledger),
        "duplicate_effects": duplicates,
        "unauthorized_effects": unauthorized,
        "unverified_confirmations": unverified_accepted,
        "rejected_unverified_responses": rejected_unverified,
        "duplicate_response_deliveries": duplicate_responses,
        "late_responses": late_responses,
        "recovered": recovered,
        "unresolved": state == "submission_unknown",
        "recovery_steps": recovery_steps,
        "recovery_duration_ms": recovery_duration_ms,
        "invariant_violations": violations,
        "trace": trace,
    }


def _environment(conn: Connection) -> dict[str, str]:
    version = conn.execute("SELECT current_setting('server_version') AS version").fetchone()["version"]
    return {"python": platform.python_version(), "platform": sys.platform,
            "postgresql": version.split(" ", 1)[0]}


def _format_run(conn: Connection, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "implementation": row["implementation"],
        "scenario": row["scenario"], "seed": row["seed"], "trials": row["trial_count"],
        "attempt_count": row["attempt_count"], "effects_created": row["effects_created"],
        "confirmed_effects": row["confirmed_effects"],
        "workflows_marked_submitted": row["workflows_marked_submitted"],
        "duplicate_effects": row["duplicate_effects"],
        "unauthorized_effects": row["unauthorized_effects"],
        "unverified_confirmations": row["unverified_confirmations"],
        "duplicate_response_deliveries": row["duplicate_response_deliveries"],
        "late_responses": row["late_responses"], "recovered_workflows": row["recovered_workflows"],
        "unresolved_workflows": row["unresolved_workflows"], "recovery_steps": row["recovery_steps"],
        "recovery_duration_ms": row["recovery_duration_ms"],
        "invariant_violations": row["invariant_violations"],
        "violation_trials": row["violation_trials"], "metrics": row["metrics"],
        "configuration": row["configuration"], "configuration_hash": row["config_hash"],
        "commit_sha": row["commit_sha"], "environment": row["environment"],
        "started_at": row["started_at"], "completed_at": row["completed_at"],
        "run_status": row["run_status"],
        "experiment_version": row["experiment_version"],
        "deterministic_schedule": row["deterministic_schedule"],
    }


def run_experiment(request: LabRunRequest, operator_username: str) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    configuration = {
        "experiment_version": EXPERIMENT_VERSION,
        "scenario": request.scenario.value,
        "implementation": request.implementation.value,
        "seed": request.seed,
        "trials": request.trials,
        "revoke_before_recovery": request.revoke_before_recovery,
        "provider_deadline_ms": PROVIDER_DEADLINE_MS,
        "lease_boundary_ms": LEASE_BOUNDARY_MS,
        "schedule": "one deterministic fault per trial; no threaded/process interleavings",
    }
    config_hash = hashlib.sha256(_canonical(configuration)).hexdigest()
    commit_sha = _commit_identity()
    key_rng = random.Random(request.seed)

    # Autocommit makes each explicit transaction below a real PostgreSQL commit.
    # In particular, the intent transaction must commit before a separate effect
    # transaction can simulate lender I/O.
    with connect_lab(autocommit=True) as conn:
        with conn.transaction():
            environment = _environment(conn)
            conn.execute(
                """INSERT INTO lab_runs(id,operator_username,seed,commit_sha,config_hash,environment,
                   implementation,scenario,trial_count,configuration,started_at,experiment_version,
                   deterministic_schedule,run_status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,'running')""",
                (run_id, operator_username, request.seed, commit_sha, config_hash, Jsonb(environment),
                 request.implementation.value, request.scenario.value, request.trials,
                 Jsonb(configuration), started, EXPERIMENT_VERSION),
            )
        trials = []
        for index in range(request.trials):
            key = f"lab-{key_rng.getrandbits(128):032x}"
            payload_hash = canonical_hash({"experiment_version": EXPERIMENT_VERSION,
                                           "seed": request.seed, "trial": index})
            if request.implementation == LabImplementation.SAFE:
                binding_digest = canonical_hash({"experiment_version": EXPERIMENT_VERSION,
                                                 "run_id": run_id, "trial": index,
                                                 "purpose": "synthetic_lab_application"})
                with conn.transaction():
                    conn.execute(
                        """INSERT INTO lab_dispatch_intents(run_id,trial_index,lender_id,idempotency_key,
                           payload_hash,approval_binding_digest) VALUES (%s,%s,%s,%s,%s,%s)""",
                        (run_id, index, LAB_LENDER_ID, key, payload_hash, binding_digest),
                    )
            with conn.transaction():
                outcome = _trial(conn, run_id, index, request, key)
            with conn.transaction():
                conn.execute("INSERT INTO lab_events(run_id,case_name,outcome,trial_index) VALUES (%s,%s,%s,%s)",
                             (run_id, request.scenario.value, Jsonb(outcome), index))
            trials.append(outcome)
        sums = {
            metric: sum(int(trial[field]) for trial in trials)
            for metric, field in {
                "attempt_count": "provider_attempts", "effects_created": "effects_created",
                "confirmed_effects": "confirmed_effects",
                "workflows_marked_submitted": "workflows_marked_submitted",
                "duplicate_effects": "duplicate_effects",
                "unauthorized_effects": "unauthorized_effects",
                "unverified_confirmations": "unverified_confirmations",
                "duplicate_response_deliveries": "duplicate_response_deliveries",
                "late_responses": "late_responses", "recovered_workflows": "recovered",
                "unresolved_workflows": "unresolved", "recovery_steps": "recovery_steps",
            }.items()
        }
        duration_total = sum(trial["recovery_duration_ms"] for trial in trials)
        violations = {name: sum(trial["invariant_violations"][name] for trial in trials)
                      for name in INVARIANTS}
        violation_trials = {name: sum(trial["invariant_violations"][name] > 0 for trial in trials)
                            for name in INVARIANTS}
        metrics = {
            "denominators": {"trials": request.trials, "provider_attempts": sums["attempt_count"]},
            "invariant_violation_rates": {
                name: {"numerator_violating_trials": violation_trials[name],
                       "denominator_trials": request.trials}
                for name in INVARIANTS
            },
            "recovery_duration_method": "sum of monotonic wall-clock time spent in local recovery logic; not provider latency",
            "recovery_duration_total_ms": duration_total,
            "recovery_duration_mean_ms_per_trial": duration_total / request.trials,
            "recovery_steps_definition": "post-unknown retry authorization, retry, or authoritative-ledger reconciliation decisions",
            "failure_rate_claim": "observed sample only; zero violations do not prove universal reliability",
        }
        completed = datetime.now(timezone.utc)
        with conn.transaction():
            row = conn.execute(
                """UPDATE lab_runs SET attempt_count=%s,effects_created=%s,confirmed_effects=%s,
                   workflows_marked_submitted=%s,
                   duplicate_effects=%s,unauthorized_effects=%s,unverified_confirmations=%s,
                   duplicate_response_deliveries=%s,late_responses=%s,recovered_workflows=%s,
                   unresolved_workflows=%s,recovery_steps=%s,recovery_duration_ms=%s,
                   invariant_violations=%s,violation_trials=%s,metrics=%s,completed_at=%s,
                   run_status='completed' WHERE id=%s RETURNING *""",
                (sums["attempt_count"], sums["effects_created"], sums["confirmed_effects"],
                 sums["workflows_marked_submitted"],
                 sums["duplicate_effects"], sums["unauthorized_effects"],
                 sums["unverified_confirmations"], sums["duplicate_response_deliveries"],
                 sums["late_responses"], sums["recovered_workflows"], sums["unresolved_workflows"],
                 sums["recovery_steps"], duration_total, Jsonb(violations), Jsonb(violation_trials),
                 Jsonb(metrics), completed, run_id),
            ).fetchone()
        return _format_run(conn, row)


def get_run(run_id: str) -> dict[str, Any] | None:
    with connect_lab() as conn:
        row = conn.execute("SELECT * FROM lab_runs WHERE id=%s", (run_id,)).fetchone()
        if not row:
            return None
        result = _format_run(conn, row)
        result["trial_outcomes"] = [event["outcome"] for event in conn.execute(
            "SELECT outcome FROM lab_events WHERE run_id=%s ORDER BY trial_index", (run_id,)).fetchall()]
        return result


def compare_runs(safe_run_id: str, unsafe_run_id: str) -> dict[str, Any]:
    with connect_lab() as conn:
        rows = conn.execute("SELECT * FROM lab_runs WHERE id=ANY(%s) AND run_status='completed'",
                            ([safe_run_id, unsafe_run_id],)).fetchall()
        by_id = {str(row["id"]): row for row in rows}
        safe = by_id.get(safe_run_id)
        unsafe = by_id.get(unsafe_run_id)
        if not safe or not unsafe:
            return {"error": "both completed lab runs must be accessible"}
        shared_config = dict(safe["configuration"])
        shared_config.pop("implementation", None)
        unsafe_config = dict(unsafe["configuration"])
        unsafe_config.pop("implementation", None)
        if safe["implementation"] != "safe" or unsafe["implementation"] != "unsafe" or \
                safe["config_hash"] == unsafe["config_hash"] or shared_config != unsafe_config:
            return {"error": "runs must be safe and unsafe executions of the same scenario, seed, trial count, and schedule"}
        numeric = ("attempt_count", "effects_created", "confirmed_effects", "workflows_marked_submitted",
                   "duplicate_effects",
                   "unauthorized_effects", "unverified_confirmations", "recovered_workflows",
                   "unresolved_workflows", "recovery_steps")
        deltas = {key: int(safe[key]) - int(unsafe[key]) for key in numeric}
        return {
            "scenario": safe["scenario"], "seed": safe["seed"], "trials": safe["trial_count"],
            "schedule_hash": hashlib.sha256(_canonical(shared_config)).hexdigest(),
            "safe_run": _format_run(conn, safe), "unsafe_run": _format_run(conn, unsafe),
            "safe_minus_unsafe": deltas,
            "comparison_note": "same deterministic fault schedule; logical results are comparable, wall-clock durations are not",
        }


def list_scenarios() -> list[dict[str, str]]:
    return [SCENARIOS[scenario] for scenario in LabFaultScenario]
