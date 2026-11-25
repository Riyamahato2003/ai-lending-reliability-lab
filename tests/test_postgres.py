import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from lending_lab.api import app
from lending_lab.clock import FrozenClock
from lending_lab.db import append_audit_event
from lending_lab.migrate import migrate
from lending_lab.policies import canonical_hash
from lending_lab.queue import claim_next_step, complete_step, retry_step
from lending_lab.simulator import submit_simulated
from lending_lab.schemas import WorkflowState
from lending_lab.workflows import ALLOWED_TRANSITIONS, evaluate_workflow, reconcile_unknown_submission, transition

pytestmark = pytest.mark.usefixtures("postgres_database")


def login(client, username, password="borrower-demo"):
    response = client.post("/demo/sessions", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_workflow(client, auth):
    response = client.post("/workflows", headers=auth, json={
        "purpose": "illustrative_offer_comparison",
        "consent_text": "I consent to synthetic illustrative lender comparison for this demo.",
        "financial_details": {"monthly_income_minor": 420000, "monthly_debt_minor": 80000,
                              "requested_amount_minor": 2400000, "term_months": 24,
                              "employment_months": 36, "region": "synthetic-west"},
    })
    assert response.status_code == 200
    return response.json()


def test_cross_borrower_access_denied():
    client = TestClient(app)
    owner = login(client, "borrower-001")
    other = login(client, "borrower-002")
    workflow = make_workflow(client, owner)
    assert client.get(f"/workflows/{workflow['id']}", headers=other).status_code == 404
    assert client.post(f"/workflows/{workflow['id']}/evaluate", headers=other).status_code == 404
    assert client.get("/operator/lab/status", headers=owner).status_code == 403
    operator = login(client, "lab-operator", "operator-demo")
    assert client.get("/operator/lab/status", headers=operator).json()["fault_injection_available"] is True
    assert client.get("/workflows/00000000-0000-0000-0000-000000000001",
                      headers=operator).status_code == 403


def test_state_machine_rejects_illegal_and_stale_transitions(db):
    assert "dispatching" in ALLOWED_TRANSITIONS["submitting"]
    assert "submission_unknown" in ALLOWED_TRANSITIONS["dispatching"]
    assert "submitted" in ALLOWED_TRANSITIONS["submission_unknown"]
    client = TestClient(app)
    auth = login(client, "borrower-001")
    workflow = make_workflow(client, auth)
    grant = db.execute("SELECT purpose,consent_text_hash,consent_text_version_id,consent_text_key_version FROM consent_grants WHERE id=%s", (workflow["consent_grant_id"],)).fetchone()
    assert grant["purpose"] == "illustrative_offer_comparison"
    from lending_lab.privacy import consent_text_hmac
    text_version = db.execute("SELECT version,consent_text FROM consent_text_versions WHERE id=%s",
                              (grant["consent_text_version_id"],)).fetchone()
    expected_hash, expected_key_version = consent_text_hmac(
        "illustrative_offer_comparison", text_version["version"], text_version["consent_text"])
    assert grant["consent_text_hash"] == expected_hash
    assert grant["consent_text_key_version"] == expected_key_version
    illegal = client.post(f"/workflows/{workflow['id']}/transition", headers=auth,
                          json={"target": "submitted", "expected_version": 0})
    assert illegal.status_code == 403
    with pytest.raises(HTTPException) as graph_error:
        transition(db, workflow["id"], "borrower-001", WorkflowState.SUBMITTED,
                   workflow["version"], "borrower-001", "test-correlation")
    assert graph_error.value.status_code == 409
    db.rollback()
    stale = client.post(f"/workflows/{workflow['id']}/transition", headers=auth,
                        json={"target": "cancelled", "expected_version": 4})
    assert stale.status_code == 409


def test_correlation_id_is_server_issued_and_cancellation_is_allowed():
    client = TestClient(app)
    auth = login(client, "borrower-001")
    workflow = make_workflow(client, auth)
    response = client.post(f"/workflows/{workflow['id']}/transition", headers={**auth, "x-correlation-id": "forged"},
                          json={"target": "cancelled", "expected_version": workflow["version"]})
    assert response.status_code == 200
    assert response.headers["x-correlation-id"] != "forged"
    assert response.json()["state"] == "cancelled"


def test_audit_immutable_and_sequences_ordered(db, owner_db):
    row = db.execute("INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,status) VALUES ('borrower-001','illustrative_offer_comparison',%s,'active') RETURNING id",
                     (hashlib.sha256(b"x").hexdigest(),)).fetchone()
    workflow = db.execute("INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) VALUES ('borrower-001',%s,'consented','p1') RETURNING id", (row["id"],)).fetchone()
    for idx in range(3):
        append_audit_event(db, workflow["id"], "test", f"event-{idx}", None, None, {"idx": idx}, "test-correlation")
    db.commit()
    events = owner_db.execute("SELECT sequence,previous_hash,event_hash FROM audit_events WHERE workflow_id=%s ORDER BY sequence", (workflow["id"],)).fetchall()
    assert [e["sequence"] for e in events] == [1, 2, 3]
    assert events[1]["previous_hash"] == events[0]["event_hash"]
    assert owner_db.execute("SELECT verify_audit_chain(%s) AS valid", (workflow["id"],)).fetchone()["valid"] is True
    owner_db.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_immutable")
    owner_db.execute("UPDATE audit_events SET action='tampered' WHERE workflow_id=%s AND sequence=1", (workflow["id"],))
    owner_db.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_immutable")
    assert owner_db.execute("SELECT verify_audit_chain(%s) AS valid", (workflow["id"],)).fetchone()["valid"] is False
    owner_db.rollback()


def test_simulator_ledger_idempotency_and_payload_conflict(db):
    payload = {"requested_amount_minor": 1000, "term_months": 12}
    first = submit_simulated(db, "cedar", "key-1", payload)
    second = submit_simulated(db, "cedar", "key-1", payload)
    assert first == second
    with pytest.raises(HTTPException) as exc:
        submit_simulated(db, "cedar", "key-1", {**payload, "term_months": 24})
    assert exc.value.status_code == 409


def test_submission_unknown_reconciliation_reuses_key(db):
    grant = db.execute("INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,status) VALUES ('borrower-001','test',%s,'active') RETURNING id",
                       (hashlib.sha256(b"x").hexdigest(),)).fetchone()
    wf = db.execute("INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) VALUES ('borrower-001',%s,'submission_unknown','p1') RETURNING id", (grant["id"],)).fetchone()
    payload = {"application": "synthetic-1"}
    assert reconcile_unknown_submission(db, str(wf["id"]), "borrower-001", "cedar", "same-key", payload)["action"] == "retry_permitted"
    original = submit_simulated(db, "cedar", "same-key", payload)
    reconciled = reconcile_unknown_submission(db, str(wf["id"]), "borrower-001", "cedar", "same-key", payload)
    assert reconciled["action"] == "reconciled"
    assert reconciled["response"] == original


def test_versioned_evaluation_snapshot_is_immutable(db, owner_db):
    client = TestClient(app)
    auth = login(client, "borrower-001")
    workflow = make_workflow(client, auth)
    response = client.post(f"/workflows/{workflow['id']}/evaluate", headers=auth)
    assert response.status_code == 200
    result = response.json()
    assert result["policy_version"] == "synthetic-policy-2026-01"
    snapshot = owner_db.execute("SELECT input_hash,policy_version FROM evaluation_snapshots WHERE id=%s", (result["snapshot_id"],)).fetchone()
    input_data = owner_db.execute("SELECT input_data FROM sensitive_evaluation_snapshot_data WHERE snapshot_id=%s",
                                  (result["snapshot_id"],)).fetchone()["input_data"]
    from lending_lab.privacy import json_hmac
    assert json_hmac("evaluation-input", input_data)[0] == snapshot["input_hash"]
    assert snapshot["policy_version"] == result["policy_version"]
    with pytest.raises(Exception, match="append-only"):
        owner_db.execute("UPDATE evaluation_snapshots SET policy_version='forged' WHERE id=%s", (result["snapshot_id"],))
    owner_db.rollback()


def test_migrations_and_seed_are_reproducible(owner_db):
    before = owner_db.execute("SELECT count(*) AS n FROM borrower_profiles").fetchone()["n"]
    migrate()
    with owner_db.cursor() as cur:
        after = cur.execute("SELECT count(*) AS n FROM borrower_profiles").fetchone()["n"]
        migrations = cur.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"]
    # Independently provisioned pilot profiles may exist if other integration
    # tests ran first. Replaying migrations must not change that count.
    assert before == after
    seeded = owner_db.execute(
        "SELECT count(*) AS n FROM borrower_profiles WHERE id IN ('borrower-001','borrower-002')"
    ).fetchone()["n"]
    assert seeded == 2
    from pathlib import Path
    expected = len(list((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")))
    assert migrations == expected


def test_postgres_step_claim_and_lease_token(db):
    grant = db.execute("INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,status) VALUES ('borrower-001','test',%s,'active') RETURNING id",
                       (hashlib.sha256(b"x").hexdigest(),)).fetchone()
    wf = db.execute("INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) VALUES ('borrower-001',%s,'consented','p1') RETURNING id", (grant["id"],)).fetchone()
    lease_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.execute("INSERT INTO workflow_steps(workflow_id,kind,payload,idempotency_key,available_at) VALUES (%s,'compare',%s,'idem',%s)", (wf["id"], Jsonb({"a": 1}), lease_start))
    claimed = claim_next_step(db, lease_seconds=60, clock=FrozenClock(lease_start))
    assert claimed["status"] == "leased" and claimed["attempts"] == 1
    assert complete_step(db, str(claimed["id"]), str(claimed["lease_token"]), {"ok": True},
                         FrozenClock(lease_start + timedelta(seconds=1)))
    assert not complete_step(db, str(claimed["id"]), str(uuid.uuid4()), {"ok": False})


def test_expired_queue_lease_is_reclaimable_with_clock(db):
    grant = db.execute("INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,status) VALUES ('borrower-001','test',%s,'active') RETURNING id",
                       (hashlib.sha256(b"x").hexdigest(),)).fetchone()
    wf = db.execute("INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) VALUES ('borrower-001',%s,'consented','p1') RETURNING id", (grant["id"],)).fetchone()
    instant = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.execute("INSERT INTO workflow_steps(workflow_id,kind,payload,idempotency_key,available_at) VALUES (%s,'test',%s,'lease-test',%s)", (wf["id"], Jsonb({}), instant))
    first = claim_next_step(db, lease_seconds=10, clock=FrozenClock(instant))
    second = claim_next_step(db, lease_seconds=10, clock=FrozenClock(instant + timedelta(seconds=11)))
    assert first["id"] == second["id"]
    assert first["lease_token"] != second["lease_token"]
    assert second["attempts"] == 2
    assert not complete_step(db, str(first["id"]), str(first["lease_token"]), {"stale_worker": True})


def test_queue_retry_backoff_and_dead_letter_after_max_attempts(db):
    grant = db.execute("INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,status) VALUES ('borrower-001','test',%s,'active') RETURNING id",
                       (hashlib.sha256(b"x").hexdigest(),)).fetchone()
    wf = db.execute("INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) VALUES ('borrower-001',%s,'consented','p1') RETURNING id", (grant["id"],)).fetchone()
    instant = datetime(2026, 1, 1, tzinfo=timezone.utc)
    step = db.execute("INSERT INTO workflow_steps(workflow_id,kind,payload,idempotency_key,max_attempts,available_at) VALUES (%s,'test',%s,'dead-letter',2,%s) RETURNING id",
                      (wf["id"], Jsonb({}), instant)).fetchone()
    first = claim_next_step(db, clock=FrozenClock(instant))
    assert retry_step(db, str(step["id"]), str(first["lease_token"]), FrozenClock(instant)) == {"status": "queued", "delay_seconds": 1}
    second = claim_next_step(db, clock=FrozenClock(instant + timedelta(seconds=1)))
    assert second["attempts"] == 2
    assert retry_step(db, str(step["id"]), str(second["lease_token"]), FrozenClock(instant + timedelta(seconds=1))) == {"status": "dead_letter", "delay_seconds": 0}
    row = db.execute("SELECT status,dead_lettered_at FROM workflow_steps WHERE id=%s", (step["id"],)).fetchone()
    assert row["status"] == "dead_letter" and row["dead_lettered_at"] is not None


def test_every_allowed_state_transition_appends_matching_audit(owner_db, db):
    for source, targets in ALLOWED_TRANSITIONS.items():
        for target in sorted(targets):
            consent = owner_db.execute(
                "INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,status) "
                "VALUES ('borrower-001','transition-test',%s,'active') RETURNING id",
                (hashlib.sha256(uuid.uuid4().bytes).hexdigest(),),
            ).fetchone()
            workflow = owner_db.execute(
                "INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) "
                "VALUES ('borrower-001',%s,%s,'transition-test-v1') RETURNING id",
                (consent["id"], source),
            ).fetchone()
            owner_db.commit()
            result = transition(db, str(workflow["id"]), "borrower-001", WorkflowState(target),
                                0, "test-transition", "transition-audit-test")
            db.commit()
            event = owner_db.execute(
                "SELECT action,from_state,to_state,sequence FROM audit_events WHERE workflow_id=%s ORDER BY sequence",
                (workflow["id"],),
            ).fetchall()
            assert result["state"] == target
            assert len(event) == 1
            assert (event[0]["action"], event[0]["from_state"], event[0]["to_state"], event[0]["sequence"]) == (
                "workflow.transition", source, target, 1)
