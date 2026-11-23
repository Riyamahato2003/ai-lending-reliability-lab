"""Checkpoint 3A approval, dispatch, recovery, and concurrency tests."""

import hashlib
import hmac
import json
import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from lending_lab.api import app
from lending_lab.approvals import approval_hmac, canonical_approval_bytes, consume_approval
from lending_lab.clock import FrozenClock
from lending_lab.db import connect, connect_migrator, run_transaction_with_retry, transaction
from lending_lab.dispatch import (DEFAULT_LEASE_SECONDS, PROVIDER_DEADLINE_SECONDS,
                                  RESULT_COMMIT_MARGIN_SECONDS, _prepare_dispatch,
                                  recover_evaluating_workflows, recover_interrupted_workflows,
                                  run_dispatch_worker_once, validate_lease_budget)
from lending_lab.policies import POLICY_VERSION, canonical_hash
from lending_lab.queue import claim_next_step
from lending_lab.schemas import WorkflowState
from lending_lab.simulator import submit_simulated

pytestmark = pytest.mark.usefixtures("postgres_database")


@pytest.fixture(autouse=True)
def clear_checkpoint3a_rows(postgres_database):
    with connect_migrator() as conn:
        conn.execute("TRUNCATE security_events,operator_resolution_events,submission_intents,approvals,"
                     "demo_sessions,audit_events,workflow_steps,lender_results,evaluation_snapshots,"
                     "workflows,consent_grants,simulator_ledger CASCADE")
        conn.execute("""INSERT INTO sensitive_borrower_data(borrower_id,input_data) VALUES
          ('borrower-001','{"monthly_income_minor":420000,"monthly_debt_minor":80000,"requested_amount_minor":2400000,"term_months":24,"employment_months":36,"region":"synthetic-west"}'),
          ('borrower-002','{"monthly_income_minor":230000,"monthly_debt_minor":120000,"requested_amount_minor":3900000,"term_months":36,"employment_months":8,"region":"synthetic-east"}')
          ON CONFLICT(borrower_id) DO UPDATE SET input_data=excluded.input_data""")


def _login(client, username="borrower-001", password="borrower-demo"):
    response = client.post("/demo/sessions", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _approved_workflow(client=None, lender="cedar", ttl=300, financial_details=None):
    client = client or TestClient(app)
    auth = _login(client)
    workflow_body = {
        "purpose": "illustrative_offer_comparison",
        "consent_text": "I consent to synthetic illustrative lender comparison for this demo.",
    }
    workflow_body["financial_details"] = financial_details or {
        "monthly_income_minor": 420000, "monthly_debt_minor": 80000,
        "requested_amount_minor": 2400000, "term_months": 24,
        "employment_months": 36, "region": "synthetic-west",
    }
    created = client.post("/workflows", headers=auth, json=workflow_body)
    assert created.status_code == 200, created.text
    workflow_id = created.json()["id"]
    evaluated = client.post(f"/workflows/{workflow_id}/evaluate", headers=auth)
    assert evaluated.status_code == 200, evaluated.text
    current = client.get(f"/workflows/{workflow_id}", headers=auth).json()
    response = client.post(f"/workflows/{workflow_id}/approvals", headers=auth, json={
        "lender_id": lender, "expected_version": current["version"],
        "approval_ttl_seconds": ttl,
    })
    assert response.status_code == 200, response.text
    return client, auth, workflow_id, response.json()


def _consume(client, auth, workflow_id, approval):
    return client.post(f"/workflows/{workflow_id}/approvals/{approval['approval_id']}/consume",
                       headers=auth, json={"expected_workflow_version": approval["workflow_version"]})


def _workflow_state(owner_db, workflow_id):
    return owner_db.execute("SELECT state,version FROM workflows WHERE id=%s", (workflow_id,)).fetchone()


def _crash_after_intent(now, exit_code):
    import os
    from lending_lab.clock import FrozenClock
    from lending_lab.dispatch import run_dispatch_worker_once
    run_dispatch_worker_once(clock=FrozenClock(now),
                             after_intent_commit=lambda _intent: os._exit(exit_code))
    os._exit(exit_code + 1)


def _crash_before_intent_commit(now, exit_code):
    import os
    from lending_lab.approvals import lock_consent_then_workflow
    from lending_lab.clock import FrozenClock
    from lending_lab.db import connect, run_transaction_with_retry
    from lending_lab.dispatch import _prepare_dispatch
    from lending_lab.queue import claim_next_step
    step = run_transaction_with_retry(lambda conn: claim_next_step(
        conn, 30, FrozenClock(now), kind="submit_application"))
    with connect() as conn:
        with conn.transaction():
            _prepare_dispatch(conn, str(step["id"]), str(step["lease_token"]),
                              FrozenClock(now), "crash-before-intent")
            os._exit(exit_code)
    os._exit(exit_code + 1)


def _crash_after_lender_effect(now, exit_code):
    import os
    from lending_lab.clock import FrozenClock
    from lending_lab.dispatch import run_dispatch_worker_once
    run_dispatch_worker_once(clock=FrozenClock(now),
                             after_lender_effect=lambda _response: os._exit(exit_code))
    os._exit(exit_code + 1)


def _crash_during_result_transaction(now, exit_code):
    import os
    from lending_lab.clock import FrozenClock
    from lending_lab.dispatch import run_dispatch_worker_once
    run_dispatch_worker_once(clock=FrozenClock(now),
                             during_result_persist=lambda _response: os._exit(exit_code))
    os._exit(exit_code + 1)


def test_approval_payload_canonical_hash_vectors():
    payload = {"term_months": 3, "schema_version": "approval-v1", "amount_minor": 10001}
    assert canonical_approval_bytes(payload) == (
        b'{"amount_minor":10001,"schema_version":"approval-v1","term_months":3}')
    digest, key_version = approval_hmac(payload)
    assert key_version == "demo-v1"
    assert digest == "1cee0b5fe55bd384a8fd1bb8725b87fb18e7f4e685ae9b2c5d5e52ef87229e6f"
    with pytest.raises(ValueError, match="floating point"):
        canonical_approval_bytes({"amount": 1.25})


def test_approval_persists_full_immutable_binding_and_expiries(owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    row = owner_db.execute("SELECT * FROM approvals WHERE id=%s", (approval["approval_id"],)).fetchone()
    payload = json.loads(row["canonical_payload"])
    assert row["borrower_id"] == "borrower-001"
    assert str(row["workflow_id"]) == workflow_id
    assert row["snapshot_hash"] == payload["snapshot_digest"]
    assert row["consent_purpose"] == "illustrative_offer_comparison"
    assert row["policy_version"] == POLICY_VERSION
    assert row["lender_version"] and row["offer_version"]
    assert row["offer_amount_minor"] == row["offer_values"]["principal_minor"]
    assert row["term_months"] == row["offer_values"]["term_months"]
    assert row["offer_expires_at"] > row["created_at"]
    assert row["approval_expires_at"] <= row["offer_expires_at"]
    with pytest.raises(psycopg.Error, match="approval binding is immutable"):
        owner_db.execute("UPDATE approvals SET payload_hmac=%s WHERE id=%s", ("0" * 64, row["id"]))
    owner_db.rollback()


def test_approval_consumption_is_atomic_single_use():
    client, auth, workflow_id, approval = _approved_workflow()
    start = threading.Barrier(2)

    def consume_concurrently():
        start.wait(timeout=10)
        try:
            with connect() as conn:
                with conn.transaction():
                    return consume_approval(conn, workflow_id, "borrower-001", approval["approval_id"],
                                             approval["workflow_version"], "borrower-001", "concurrent-consume")
        except HTTPException as exc:
            return {"error": exc.status_code}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume_concurrently(), range(2)))
    assert sum(result.get("status") == "consumed" for result in results) == 1
    assert sum(result.get("error") == 409 for result in results) == 1
    with connect_migrator() as owner:
        approval_row = owner.execute("SELECT status,count(*) OVER() AS n FROM approvals WHERE id=%s",
                                     (approval["approval_id"],)).fetchone()
        assert approval_row["status"] == "consumed" and approval_row["n"] == 1


def test_approval_expiry_checked_at_consumption_and_dispatch(owner_db):
    client, auth, workflow_id, approval = _approved_workflow(ttl=1)
    expires = datetime.fromisoformat(approval["approval_expires_at"].replace("Z", "+00:00"))
    from lending_lab.approvals import consume_approval
    result = run_transaction_with_retry(lambda conn: consume_approval(
        conn, workflow_id, "borrower-001", approval["approval_id"], approval["workflow_version"],
        "borrower-001", "expired-consume", FrozenClock(expires + timedelta(seconds=1))))
    assert result["error"] == "approval_or_offer_expired_at_consumption"
    row = owner_db.execute("SELECT status FROM approvals WHERE id=%s", (approval["approval_id"],)).fetchone()
    assert row["status"] == "expired"

    client2, auth2, workflow2, approval2 = _approved_workflow(ttl=1)
    consumed = _consume(client2, auth2, workflow2, approval2)
    assert consumed.status_code == 200
    expires2 = datetime.fromisoformat(approval2["approval_expires_at"].replace("Z", "+00:00"))
    called = []
    def should_not_send(*_args):
        called.append(True)
        return {"simulated_application_id": "unexpected"}
    result2 = run_dispatch_worker_once(clock=FrozenClock(expires2 + timedelta(seconds=1)),
                                       sender=should_not_send)
    assert result2["status"] == "failed"
    assert called == []
    assert _workflow_state(owner_db, workflow2)["state"] == "failed"


def test_material_change_invalidates_approval(owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    owner_db.execute("UPDATE workflows SET policy_version='changed-policy' WHERE id=%s", (workflow_id,))
    owner_db.commit()
    response = _consume(client, auth, workflow_id, approval)
    assert response.status_code == 409
    assert _workflow_state(owner_db, workflow_id)["state"] == "failed"
    row = owner_db.execute("SELECT status,invalidation_reason FROM approvals WHERE id=%s",
                           (approval["approval_id"],)).fetchone()
    assert row["status"] == "invalidated"


def test_approval_explicit_authorization_owner_checks_and_reuse(owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    other = _login(client, "borrower-002")
    assert client.post(f"/workflows/{workflow_id}/approvals/{approval['approval_id']}/consume",
                       headers=other,
                       json={"expected_workflow_version": approval["workflow_version"]}).status_code == 404
    operator = _login(client, "lab-operator", "operator-demo")
    assert client.post(f"/workflows/{workflow_id}/approvals/{approval['approval_id']}/consume",
                       headers=operator,
                       json={"expected_workflow_version": approval["workflow_version"]}).status_code == 403
    approved = _consume(client, auth, workflow_id, approval)
    assert approved.status_code == 200 and approved.json()["state"] == "submitting"
    repeated = _consume(client, auth, workflow_id, approval)
    assert repeated.status_code == 409
    row = owner_db.execute("SELECT status,consumed_at FROM approvals WHERE id=%s",
                           (approval["approval_id"],)).fetchone()
    assert row["status"] == "consumed" and row["consumed_at"] is not None


def test_dispatch_intent_commits_before_external_send(owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    assert _consume(client, auth, workflow_id, approval).status_code == 200
    observed = {}
    sender_errors = []

    def sender(lender_id, key, payload):
        try:
            with connect() as conn:
                intent = conn.execute("SELECT * FROM submission_intents WHERE workflow_id=%s", (workflow_id,)).fetchone()
                state = conn.execute("SELECT state FROM workflows WHERE id=%s", (workflow_id,)).fetchone()["state"]
                saved_payload = conn.execute(
                    "SELECT lender_payload FROM sensitive_dispatch_payloads WHERE intent_id=%s",
                    (intent["id"],)).fetchone()["lender_payload"]
            with connect_migrator() as audit_conn:
                audit = audit_conn.execute("SELECT action,from_state,to_state FROM audit_events WHERE workflow_id=%s "
                                           "ORDER BY sequence DESC LIMIT 1", (workflow_id,)).fetchone()
            observed.update(intent_exists=intent is not None, state=state, audit=audit)
            assert intent["idempotency_key"] == key
            assert intent["lender_id"] == lender_id
            assert saved_payload == payload
            assert state == "dispatching"
            assert audit["action"] == "submission.dispatch_intent_committed"
            with transaction() as lender_db:
                return submit_simulated(lender_db, lender_id, key, payload)
        except Exception as exc:
            sender_errors.append(repr(exc))
            raise

    result = run_dispatch_worker_once(sender=sender)
    assert not sender_errors, sender_errors
    assert result["status"] == "submitted"
    assert observed["intent_exists"] is True
    assert _workflow_state(owner_db, workflow_id)["state"] == "submitted"
    events = owner_db.execute("SELECT action,from_state,to_state FROM audit_events WHERE workflow_id=%s ORDER BY sequence",
                              (workflow_id,)).fetchall()
    assert any(event["action"] == "approval.created" for event in events)
    assert any(event["action"] == "approval.consumed" for event in events)
    assert any(event["action"] == "submission.dispatch_intent_committed" for event in events)
    assert any(event["action"] == "submission.confirmed" for event in events)


def test_restart_reconciles_dispatching_and_unknown(owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    assert _consume(client, auth, workflow_id, approval).status_code == 200
    now = datetime.now(timezone.utc) + timedelta(seconds=2)
    with pytest.raises(SystemExit):
        run_dispatch_worker_once(clock=FrozenClock(now),
                                 after_intent_commit=lambda _intent: (_ for _ in ()).throw(SystemExit(77)))
    assert _workflow_state(owner_db, workflow_id)["state"] == "dispatching"
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger").fetchone()["n"] == 0
    recovered = run_dispatch_worker_once(clock=FrozenClock(now + timedelta(seconds=31)))
    assert recovered["status"] == "submitted"
    assert _workflow_state(owner_db, workflow_id)["state"] == "submitted"
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger WHERE lender_id='cedar'").fetchone()["n"] == 1

    client2, auth2, workflow2, approval2 = _approved_workflow()
    assert _consume(client2, auth2, workflow2, approval2).status_code == 200
    now2 = datetime.now(timezone.utc) + timedelta(seconds=2)
    lost = run_dispatch_worker_once(clock=FrozenClock(now2), sender=lambda *_args: (_ for _ in ()).throw(RuntimeError("response lost")))
    assert lost["status"] == "submission_unknown"
    assert _workflow_state(owner_db, workflow2)["state"] == "submission_unknown"
    recovered2 = run_dispatch_worker_once(clock=FrozenClock(now2 + timedelta(seconds=2)))
    assert recovered2["status"] == "submitted"
    assert _workflow_state(owner_db, workflow2)["state"] == "submitted"


@pytest.mark.parametrize("mutation,reason", [
    (lambda value: {**value, "lender_id": "birch"}, "response_lender_mismatch"),
    (lambda value: {**value, "payload_hash": "0" * 64}, "response_payload_hash_mismatch"),
    (lambda value: {key: item for key, item in value.items() if key != "status"}, "response_schema_mismatch"),
    (lambda value: {**value, "status": "rejected"}, "unsupported_response_status"),
    (lambda value: {**value, "simulated_application_id": "not-an-id"}, "invalid_simulated_application_id"),
    (lambda value: {**value, "simulated_application_id": "0" * 20}, "simulated_application_id_mismatch"),
    (lambda value: {**value, "is_real_submission": "false"}, "unsupported_response_status"),
])
def test_malformed_or_contradictory_lender_response_stays_unknown(owner_db, mutation, reason):
    client, auth, workflow_id, approval = _approved_workflow()
    assert _consume(client, auth, workflow_id, approval).status_code == 200

    def contradictory_sender(lender_id, key, payload):
        with transaction() as lender_conn:
            authoritative = submit_simulated(lender_conn, lender_id, key, payload)
        return mutation(authoritative)

    first = run_dispatch_worker_once(sender=contradictory_sender)
    assert first["status"] == "submission_unknown"
    assert first["reason"] == reason
    assert _workflow_state(owner_db, workflow_id)["state"] == "submission_unknown"
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger").fetchone()["n"] == 1

    # A later worker uses the authoritative row, not the malformed transport response.
    now = datetime.now(timezone.utc) + timedelta(seconds=3)
    recovered = run_dispatch_worker_once(clock=FrozenClock(now))
    assert recovered["status"] == "submitted"
    assert _workflow_state(owner_db, workflow_id)["state"] == "submitted"


def test_missing_authoritative_ledger_never_confirms(owner_db):
    from lending_lab.dispatch import _confirmation_error
    payload = {}
    payload_hash = canonical_hash(payload)
    intent = {"lender_id": "cedar", "idempotency_key": "no-ledger", "lender_payload": payload,
              "payload_hash": payload_hash}
    assert _confirmation_error(intent, {
        "simulated_application_id": canonical_hash({"lender": "cedar", "key": "no-ledger"})[:20],
        "status": "accepted_for_simulation", "lender_id": "cedar", "payload_hash": payload_hash,
        "is_real_submission": False,
    }, None) == "simulator_ledger_confirmation_missing"


def test_revoked_consent_blocks_new_send_but_allows_existing_intent_reconciliation(owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    assert _consume(client, auth, workflow_id, approval).status_code == 200
    now = datetime.now(timezone.utc) + timedelta(seconds=2)
    # A revoked grant after intent commit but before the final send check must prevent
    # a new lender effect. This exercises the Checkpoint 3B boundary without implementing
    # cross-workflow revocation or deletion.
    grant_id = owner_db.execute("SELECT consent_grant_id FROM workflows WHERE id=%s", (workflow_id,)).fetchone()["consent_grant_id"]
    def revoke_after_intent(_intent):
        revoked = client.post(f"/consents/{grant_id}/revoke", headers=auth)
        assert revoked.status_code == 200, revoked.text

    called = []
    blocked = run_dispatch_worker_once(clock=FrozenClock(now), after_intent_commit=revoke_after_intent,
                                       sender=lambda *_args: called.append(True))
    assert blocked["status"] == "unresolved"
    assert called == []
    assert _workflow_state(owner_db, workflow_id)["state"] == "submission_unknown"
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger").fetchone()["n"] == 0

    # A different, still-active workflow commits its effect before withdrawal.
    # The response is lost; recovery after deletion reconciles by the original key.
    client2, auth2, workflow2, approval2 = _approved_workflow(financial_details={
        "monthly_income_minor": 420000, "monthly_debt_minor": 80000,
        "requested_amount_minor": 2400000, "term_months": 24,
        "employment_months": 36, "region": "synthetic-west",
    })
    assert _consume(client2, auth2, workflow2, approval2).status_code == 200
    grant2 = owner_db.execute("SELECT consent_grant_id FROM workflows WHERE id=%s", (workflow2,)).fetchone()["consent_grant_id"]
    callback_observations = []
    def effect_then_revoke(lender_id, key, payload):
        with transaction() as lender_conn:
            try:
                submit_simulated(lender_conn, lender_id, key, payload)
            except Exception as exc:
                callback_observations.append(("sender_error", repr(exc)))
                raise
        revoked = client2.post(f"/consents/{grant2}/revoke", headers=auth2)
        callback_observations.append((revoked.status_code, revoked.text))
        if revoked.status_code != 200:
            raise RuntimeError(f"revoke failed: {revoked.status_code}: {revoked.text}")
        raise RuntimeError("simulated response lost after effect and concurrent consent withdrawal")
    now2 = datetime.now(timezone.utc) + timedelta(seconds=2)
    result = run_dispatch_worker_once(clock=FrozenClock(now2), sender=effect_then_revoke)
    assert result.get("status") == "submission_unknown", result
    assert callback_observations and callback_observations[0][0] == 200, (
        callback_observations, result, _workflow_state(owner_db, workflow2),
        owner_db.execute("SELECT status FROM workflow_steps WHERE workflow_id=%s", (workflow2,)).fetchone())
    assert _workflow_state(owner_db, workflow2)["state"] == "submission_unknown"
    assert owner_db.execute("SELECT count(*) AS n FROM sensitive_borrower_data WHERE borrower_id='borrower-001'").fetchone()["n"] == 0
    from lending_lab.dispatch import recover_interrupted_workflows
    recovered = recover_interrupted_workflows(clock=FrozenClock(now2 + timedelta(seconds=3)))
    assert recovered["revoked_intents_reconciled"] == 1
    assert _workflow_state(owner_db, workflow2)["state"] == "submitted"
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger").fetchone()["n"] == 1
    events = owner_db.execute("SELECT action FROM audit_events WHERE workflow_id=%s ORDER BY sequence",
                              (workflow2,)).fetchall()
    assert [row["action"] for row in events].count("submission.dispatch_intent_committed") == 1
    assert [row["action"] for row in events].count("submission.confirmed") == 1


def test_stale_worker_idempotency_and_concurrent_double_submit(owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    assert _consume(client, auth, workflow_id, approval).status_code == 200
    start_time = datetime.now(timezone.utc) + timedelta(seconds=2)
    send_barrier = threading.Barrier(2)
    first_entered = threading.Event()
    responses = []
    errors = []

    def before_send(_intent):
        first_entered.set()
        send_barrier.wait(timeout=15)

    def sender(lender_id, key, payload):
        with transaction() as lender_conn:
            response = submit_simulated(lender_conn, lender_id, key, payload)
        responses.append(response)
        return response

    def old_worker():
        try:
            return run_dispatch_worker_once(clock=FrozenClock(start_time), lease_seconds=16,
                                            sender=sender, before_lender_send=before_send)
        except Exception as exc:
            errors.append(exc)
            raise

    def expired_lease_worker():
        try:
            assert first_entered.wait(timeout=10)
            return run_dispatch_worker_once(clock=FrozenClock(start_time + timedelta(seconds=17)),
                                            lease_seconds=16, sender=sender,
                                            before_lender_send=before_send)
        except Exception as exc:
            errors.append(exc)
            raise

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="worker") as pool:
        first = pool.submit(old_worker)
        # Rename the submitting worker thread for deterministic barrier coordination.
        first_worker = first
        second = pool.submit(expired_lease_worker)
        first_result, second_result = first_worker.result(timeout=20), second.result(timeout=20)
    assert not errors
    assert {first_result["status"], second_result["status"]} <= {
        "submitted", "confirmed_by_stale_worker", "unresolved"}
    assert len(responses) == 1
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger WHERE lender_id='cedar' AND idempotency_key=%s",
                            (owner_db.execute("SELECT idempotency_key FROM approvals WHERE id=%s",
                                              (approval["approval_id"],)).fetchone()["idempotency_key"],)).fetchone()["n"] == 1
    assert _workflow_state(owner_db, workflow_id)["state"] == "submitted"


def test_expired_worker_effect_is_reconciled_without_stale_lease_ack(owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    assert _consume(client, auth, workflow_id, approval).status_code == 200
    start = datetime.now(timezone.utc) + timedelta(seconds=2)
    effect_committed = threading.Event()
    allow_old_worker_to_finish = threading.Event()
    old_results = []
    old_errors = []

    def sender(lender_id, key, payload):
        with transaction() as conn:
            return submit_simulated(conn, lender_id, key, payload)

    def pause_after_effect(_response):
        effect_committed.set()
        assert allow_old_worker_to_finish.wait(timeout=15)

    def old_worker():
        try:
            old_results.append(run_dispatch_worker_once(
                clock=FrozenClock(start), lease_seconds=16, sender=sender,
                after_lender_effect=pause_after_effect))
        except Exception as exc:
            old_errors.append(exc)

    thread = threading.Thread(target=old_worker, name="expired-effect-worker")
    thread.start()
    assert effect_committed.wait(timeout=10)
    old_step = owner_db.execute("SELECT id,lease_token FROM workflow_steps WHERE workflow_id=%s",
                                (workflow_id,)).fetchone()
    # The old worker has committed the simulator effect but has not persisted the response.
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger").fetchone()["n"] == 1
    replacement = run_transaction_with_retry(lambda conn: claim_next_step(
        conn, 30, FrozenClock(start + timedelta(seconds=17)), kind="submit_application"))
    assert replacement and replacement["status"] == "leased"
    assert str(replacement["lease_token"]) != str(old_step["lease_token"])

    from lending_lab.dispatch import process_leased_step
    replacement_result = process_leased_step(replacement,
                                             clock=FrozenClock(start + timedelta(seconds=17)))
    assert replacement_result["status"] == "submitted"
    row = owner_db.execute("SELECT status,lease_token FROM workflow_steps WHERE id=%s",
                           (replacement["id"],)).fetchone()
    assert row["status"] == "done" and row["lease_token"] is None
    allow_old_worker_to_finish.set()
    thread.join(timeout=15)
    assert not thread.is_alive() and not old_errors
    assert old_results[0]["status"] in {"submitted", "confirmed_by_stale_worker"}
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger").fetchone()["n"] == 1
    actions = owner_db.execute("SELECT action FROM audit_events WHERE workflow_id=%s ORDER BY sequence",
                               (workflow_id,)).fetchall()
    assert [item["action"] for item in actions].count("submission.confirmed") == 1
    assert _workflow_state(owner_db, workflow_id)["state"] == "submitted"


def test_concurrent_queue_claims_use_skip_locked(db):
    now = datetime.now(timezone.utc)
    ids = []
    for index in range(2):
        grant = db.execute("INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,status) "
                           "VALUES ('borrower-001','claim-test',%s,'active') RETURNING id",
                           (hashlib.sha256(f'grant-{index}'.encode()).hexdigest(),)).fetchone()
        workflow = db.execute("INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) "
                              "VALUES ('borrower-001',%s,'consented',%s) RETURNING id",
                              (grant["id"], POLICY_VERSION)).fetchone()
        step = db.execute("INSERT INTO workflow_steps(workflow_id,kind,payload,idempotency_key,available_at) "
                          "VALUES (%s,'submit_application',%s,%s,%s) RETURNING id",
                          (workflow["id"], Jsonb({}), f'claim-{index}', now)).fetchone()
        ids.append(str(step["id"]))
    db.commit()
    a_claimed = threading.Event()
    b_claimed = threading.Event()
    claims = []

    def worker_a():
        with connect() as conn:
            with conn.transaction():
                claim = claim_next_step(conn, 30, FrozenClock(now), kind="submit_application")
                claims.append(str(claim["id"]))
                a_claimed.set()
                assert b_claimed.wait(timeout=10)

    def worker_b():
        assert a_claimed.wait(timeout=10)
        with connect() as conn:
            with conn.transaction():
                claim = claim_next_step(conn, 30, FrozenClock(now), kind="submit_application")
                claims.append(str(claim["id"]))
                b_claimed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker_a), pool.submit(worker_b)]
        for future in futures:
            future.result(timeout=15)
    assert len(set(claims)) == 2
    assert set(claims) == set(ids)


def test_restart_recovers_interrupted_evaluation(owner_db):
    grant = owner_db.execute("INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,status) "
                             "VALUES ('borrower-001','illustrative_offer_comparison',%s,'active') RETURNING id",
                             (hashlib.sha256(b'evaluation-recovery-consent').hexdigest(),)).fetchone()
    workflow = owner_db.execute("INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) "
                                "VALUES ('borrower-001',%s,'evaluating',%s) RETURNING id",
                                (grant["id"], POLICY_VERSION)).fetchone()
    profile = owner_db.execute("SELECT input_data FROM sensitive_borrower_data WHERE borrower_id='borrower-001'").fetchone()
    from lending_lab.privacy import json_hmac
    snapshot_hash = json_hmac("evaluation-input", profile["input_data"])[0]
    snapshot = owner_db.execute("INSERT INTO evaluation_snapshots(workflow_id,input_hash,policy_version) "
                                "VALUES (%s,%s,%s) RETURNING id",
                                (workflow["id"], snapshot_hash, POLICY_VERSION)).fetchone()
    owner_db.execute("INSERT INTO sensitive_evaluation_snapshot_data(snapshot_id,input_data) VALUES (%s,%s)",
                     (snapshot["id"], Jsonb(profile["input_data"])))
    owner_db.commit()
    assert recover_evaluating_workflows() == 1
    row = owner_db.execute("SELECT state FROM workflows WHERE id=%s", (workflow["id"],)).fetchone()
    results = owner_db.execute("SELECT count(*) AS n FROM lender_results WHERE workflow_id=%s AND snapshot_id=%s",
                               (workflow["id"], snapshot["id"])).fetchone()["n"]
    assert row["state"] == "compared" and results == 3
    event = owner_db.execute("SELECT action FROM audit_events WHERE workflow_id=%s ORDER BY sequence DESC LIMIT 1",
                             (workflow["id"],)).fetchone()
    assert event["action"] == "workflow.evaluation_recovered"


def test_bounded_deadlock_retry_on_real_postgresql(owner_db):
    workflows = []
    for index in range(2):
        grant = owner_db.execute("INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,status) "
                                 "VALUES ('borrower-001','deadlock-test',%s,'active') RETURNING id",
                                 (hashlib.sha256(f'deadlock-{index}'.encode()).hexdigest(),)).fetchone()
        workflow = owner_db.execute("INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) "
                                    "VALUES ('borrower-001',%s,'consented',%s) RETURNING id",
                                    (grant["id"], POLICY_VERSION)).fetchone()
        workflows.append(str(workflow["id"]))
    owner_db.commit()
    gate = threading.Barrier(2)
    counts = {}
    count_lock = threading.Lock()

    def make_operation(index):
        def operation(conn):
            name = threading.current_thread().name
            with count_lock:
                counts[name] = counts.get(name, 0) + 1
                attempt = counts[name]
            order = workflows if index == 0 else list(reversed(workflows))
            if attempt == 1:
                conn.execute("SELECT id FROM workflows WHERE id=%s FOR UPDATE", (order[0],))
                gate.wait(timeout=10)
                conn.execute("SELECT id FROM workflows WHERE id=%s FOR UPDATE", (order[1],))
            else:
                for workflow_id in sorted(workflows):
                    conn.execute("SELECT id FROM workflows WHERE id=%s FOR UPDATE", (workflow_id,))
            return True
        return operation

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_transaction_with_retry, make_operation(index)) for index in range(2)]
        assert [future.result(timeout=15) for future in futures] == [True, True]
    assert max(counts.values()) <= 3
    assert sum(counts.values()) == 3


def test_stale_lease_is_rejected_before_send(owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    assert _consume(client, auth, workflow_id, approval).status_code == 200
    now = datetime.now(timezone.utc) + timedelta(seconds=2)
    step = run_transaction_with_retry(lambda conn: claim_next_step(
        conn, 16, FrozenClock(now), kind="submit_application"))
    called = []
    from lending_lab.dispatch import process_leased_step
    result = process_leased_step(step, clock=FrozenClock(now + timedelta(seconds=17)),
                                 sender=lambda *_args: called.append(True))
    assert result["status"] == "stale_lease"
    assert called == []
    assert _workflow_state(owner_db, workflow_id)["state"] == "submitting"


@pytest.mark.parametrize("crash_stage,exit_code", [
    ("before_intent_commit", 81),
    ("after_intent_commit", 82),
    ("after_lender_effect", 83),
    ("during_result_persistence", 84),
])
def test_worker_process_crash_and_restart_has_no_duplicate_effect(crash_stage, exit_code, owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    assert _consume(client, auth, workflow_id, approval).status_code == 200
    now = datetime.now(timezone.utc) + timedelta(seconds=2)
    targets = {
        "before_intent_commit": _crash_before_intent_commit,
        "after_intent_commit": _crash_after_intent,
        "after_lender_effect": _crash_after_lender_effect,
        "during_result_persistence": _crash_during_result_transaction,
    }
    process = multiprocessing.get_context("spawn").Process(target=targets[crash_stage], args=(now, exit_code))
    process.start()
    process.join(timeout=20)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    assert process.exitcode == exit_code
    before = _workflow_state(owner_db, workflow_id)["state"]
    expected_before = "submitting" if crash_stage == "before_intent_commit" else "dispatching"
    assert before == expected_before
    # PostgreSQL may need a short interval to observe the abrupt child-process
    # disconnect and release its row lock. A production worker polls repeatedly;
    # model that bounded restart polling instead of treating one SKIP LOCKED
    # idle result as terminal recovery failure.
    recovery_clock = FrozenClock(now + timedelta(seconds=31))
    deadline = time.monotonic() + 2
    restarted = recover_interrupted_workflows(clock=recovery_clock)
    while restarted["submission_steps_processed"] == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
        restarted = recover_interrupted_workflows(clock=recovery_clock)
    assert restarted["submission_steps_processed"] >= 1
    assert _workflow_state(owner_db, workflow_id)["state"] == "submitted"
    key = owner_db.execute("SELECT idempotency_key FROM approvals WHERE id=%s", (approval["approval_id"],)).fetchone()["idempotency_key"]
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger WHERE lender_id='cedar' AND idempotency_key=%s",
                            (key,)).fetchone()["n"] == 1


def test_operator_conflict_review_is_restricted_and_immutable(owner_db):
    client, auth, workflow_id, approval = _approved_workflow()
    assert _consume(client, auth, workflow_id, approval).status_code == 200

    def create_conflicting_lender_result(intent):
        with transaction() as conn:
            conflicting = {"different": "payload"}
            digest = canonical_hash(conflicting)
            app_id = canonical_hash({"lender": intent["lender_id"], "key": intent["idempotency_key"]})[:20]
            conn.execute("""INSERT INTO simulator_ledger(lender_id,idempotency_key,payload_hash,response)
                            VALUES (%s,%s,%s,%s)""",
                         (intent["lender_id"], intent["idempotency_key"], digest,
                          Jsonb({"simulated_application_id": app_id, "status": "accepted_for_simulation",
                                 "lender_id": intent["lender_id"], "payload_hash": digest,
                                 "is_real_submission": False})))

    result = run_dispatch_worker_once(after_intent_commit=create_conflicting_lender_result)
    assert result["status"] == "conflict"
    operator = _login(client, "lab-operator", "operator-demo")
    borrower_conflicts = client.get("/operator/reconciliation-conflicts", headers=auth)
    assert borrower_conflicts.status_code == 403
    listed = client.get("/operator/reconciliation-conflicts", headers=operator)
    assert listed.status_code == 200
    conflict = next(item for item in listed.json()["conflicts"] if item["workflow_id"] == workflow_id)
    assert conflict["expected_payload_hash"] != conflict["observed_payload_hash"]
    review = client.post(f"/operator/reconciliation-conflicts/{conflict['intent_id']}/reviews",
                         headers=operator, json={"disposition": "evidence_recorded",
                                                  "evidence": "Synthetic ledger payload mismatch reviewed."})
    assert review.status_code == 200 and review.json()["status"] == "recorded_unresolved"
    assert _workflow_state(owner_db, workflow_id)["state"] == "submission_unknown"
    row = owner_db.execute("SELECT evidence FROM operator_resolution_events WHERE id=%s",
                           (review.json()["review_id"],)).fetchone()
    assert row["evidence"] == "Synthetic ledger payload mismatch reviewed."


def test_lease_budget_requires_provider_deadline_margin():
    assert DEFAULT_LEASE_SECONDS > PROVIDER_DEADLINE_SECONDS + RESULT_COMMIT_MARGIN_SECONDS
    with pytest.raises(ValueError, match="lease must exceed"):
        validate_lease_budget(PROVIDER_DEADLINE_SECONDS + RESULT_COMMIT_MARGIN_SECONDS)
