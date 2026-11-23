"""Checkpoint 3B consent revocation and privacy tests against PostgreSQL."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from lending_lab.api import app
from lending_lab.clock import FrozenClock
from lending_lab.db import connect, connect_migrator, run_transaction_with_retry, transaction
from lending_lab.dispatch import recover_interrupted_workflows, run_dispatch_worker_once
from lending_lab.policies import POLICY_VERSION
from lending_lab.queue import claim_next_step
from lending_lab.simulator import submit_simulated

pytestmark = pytest.mark.usefixtures("postgres_database")
CONSENT_TEXT = "I consent to synthetic illustrative lender comparison for this demo."


@pytest.fixture(autouse=True)
def reset_3b_data(postgres_database):
    with connect_migrator() as conn:
        conn.execute("""TRUNCATE security_events,operator_resolution_events,sensitive_approval_data,
          sensitive_dispatch_payloads,sensitive_lender_result_data,sensitive_evaluation_snapshot_data,
          sensitive_borrower_data,submission_intents,approvals,demo_sessions,lab_events,lab_runs,
          audit_events,workflow_steps,lender_results,evaluation_snapshots,workflows,consent_grants,
          simulator_ledger CASCADE""")
        conn.execute("INSERT INTO sensitive_borrower_data(borrower_id,input_data) VALUES (%s,%s),(%s,%s)",
                     ("borrower-001", Jsonb({"monthly_income_minor": 420000, "monthly_debt_minor": 80000,
                         "requested_amount_minor": 2400000, "term_months": 24,
                         "employment_months": 36, "region": "synthetic-west"}),
                      "borrower-002", Jsonb({"monthly_income_minor": 230000, "monthly_debt_minor": 120000,
                         "requested_amount_minor": 3900000, "term_months": 36,
                         "employment_months": 8, "region": "synthetic-east"})))


def login(client, username="borrower-001"):
    password = "operator-demo" if username == "lab-operator" else "borrower-demo"
    response = client.post("/demo/sessions", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_workflow(client, auth, financial_details=None):
    data = {"purpose": "illustrative_offer_comparison", "consent_text": CONSENT_TEXT}
    if financial_details:
        data["financial_details"] = financial_details
    response = client.post("/workflows", headers=auth, json=data)
    assert response.status_code == 200, response.text
    return response.json()


def approve_workflow(client, auth):
    wf = create_workflow(client, auth)
    grant_id = wf["consent_grant_id"]
    evaluated = client.post(f"/workflows/{wf['id']}/evaluate", headers=auth)
    assert evaluated.status_code == 200, evaluated.text
    wf = client.get(f"/workflows/{wf['id']}", headers=auth).json()
    wf["consent_grant_id"] = grant_id
    approval = client.post(f"/workflows/{wf['id']}/approvals", headers=auth,
                           json={"lender_id": "cedar", "expected_version": wf["version"]})
    assert approval.status_code == 200, approval.text
    consumed = client.post(f"/workflows/{wf['id']}/approvals/{approval.json()['approval_id']}/consume",
                           headers=auth,
                           json={"expected_workflow_version": approval.json()["workflow_version"]})
    assert consumed.status_code == 200, consumed.text
    return wf, approval.json()


def revoke(client, auth, wf):
    return client.post(f"/consents/{wf['consent_grant_id']}/revoke", headers=auth)


def test_revocation_before_approval_and_after_approval_invalidates_unused_approval(owner_db):
    client, auth = TestClient(app), None
    auth = login(client)
    before = create_workflow(client, auth)
    response = revoke(client, auth, before)
    assert response.status_code == 200 and response.json()["affected_workflows"] == 1
    assert client.post(f"/workflows/{before['id']}/evaluate", headers=auth).status_code == 409
    assert owner_db.execute("SELECT state FROM workflows WHERE id=%s", (before["id"],)).fetchone()["state"] == "consent_revoked"

    awaiting = create_workflow(client, auth, {
        "monthly_income_minor": 420000, "monthly_debt_minor": 80000,
        "requested_amount_minor": 2400000, "term_months": 24,
        "employment_months": 36, "region": "synthetic-west",
    })
    assert client.post(f"/workflows/{awaiting['id']}/evaluate", headers=auth).status_code == 200
    current = client.get(f"/workflows/{awaiting['id']}", headers=auth).json()
    approval = client.post(f"/workflows/{awaiting['id']}/approvals", headers=auth,
                           json={"lender_id": "cedar", "expected_version": current["version"]}).json()
    response = revoke(client, auth, awaiting)
    assert response.status_code == 200
    row = owner_db.execute("SELECT status,canonical_payload,offer_values FROM approvals WHERE id=%s",
                           (approval["approval_id"],)).fetchone()
    assert row == {"status": "invalidated", "canonical_payload": None, "offer_values": None}
    assert owner_db.execute("SELECT count(*) AS n FROM sensitive_approval_data WHERE approval_id=%s",
                            (approval["approval_id"],)).fetchone()["n"] == 0
    assert client.post(f"/workflows/{awaiting['id']}/approvals/{approval['approval_id']}/consume",
                       headers=auth, json={"expected_workflow_version": approval["workflow_version"]}).status_code == 409


def test_revocation_after_intent_blocks_send_and_recovery_never_retries_without_effect(owner_db):
    client = TestClient(app)
    auth = login(client)
    wf, approval = approve_workflow(client, auth)
    now = datetime.now(timezone.utc) + timedelta(seconds=2)
    step = run_transaction_with_retry(lambda conn: claim_next_step(
        conn, 30, FrozenClock(now), kind="submit_application"))
    from lending_lab.dispatch import _prepare_dispatch
    intent_result = run_transaction_with_retry(lambda conn: _prepare_dispatch(
        conn, str(step["id"]), str(step["lease_token"]), FrozenClock(now), "3b-before-send"))
    intent = intent_result["intent"]
    send_calls = []
    response = revoke(client, auth, wf)
    assert response.status_code == 200
    assert response.json()["borrower_data_deleted"] is True
    assert owner_db.execute("SELECT payload_hash,idempotency_key FROM submission_intents WHERE id=%s",
                            (intent["id"],)).fetchone()["idempotency_key"] == intent["idempotency_key"]
    assert owner_db.execute("SELECT count(*) AS n FROM sensitive_dispatch_payloads WHERE intent_id=%s",
                            (intent["id"],)).fetchone()["n"] == 0
    assert owner_db.execute("SELECT count(*) AS n FROM sensitive_evaluation_snapshot_data").fetchone()["n"] == 0
    assert owner_db.execute("SELECT count(*) AS n FROM sensitive_lender_result_data").fetchone()["n"] == 0
    assert owner_db.execute("SELECT count(*) AS n FROM sensitive_borrower_data WHERE borrower_id='borrower-001'").fetchone()["n"] == 0

    # Reclaimed/stale processing can consult the ledger but cannot create a new effect.
    result = run_dispatch_worker_once(clock=FrozenClock(now + timedelta(seconds=1)),
                                      sender=lambda *args: send_calls.append(args))
    assert result["status"] == "idle"
    recovered = recover_interrupted_workflows(clock=FrozenClock(now + timedelta(seconds=2)))
    assert recovered["revoked_intents_reconciled"] == 0
    assert send_calls == []
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger").fetchone()["n"] == 0
    assert owner_db.execute("SELECT state FROM workflows WHERE id=%s", (wf["id"],)).fetchone()["state"] == "submission_unknown"
    events = owner_db.execute("SELECT action FROM audit_events WHERE workflow_id=%s ORDER BY sequence",
                              (wf["id"],)).fetchall()
    assert events[-1]["action"] == "consent.revoked_reconciliation_only"
    assert owner_db.execute("SELECT verify_audit_chain(%s) AS ok", (wf["id"],)).fetchone()["ok"] is True


def test_revoke_while_request_is_in_flight_prevents_simulated_effect(owner_db):
    client = TestClient(app)
    auth = login(client)
    wf, _approval = approve_workflow(client, auth)
    began = threading.Event()
    proceed = threading.Event()
    results, errors = [], []
    now = datetime.now(timezone.utc) + timedelta(seconds=2)

    def sender(lender_id, key, payload):
        began.set()
        assert proceed.wait(timeout=10)
        with transaction() as conn:
            return submit_simulated(conn, lender_id, key, payload)

    def worker():
        try:
            results.append(run_dispatch_worker_once(clock=FrozenClock(now), sender=sender))
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        assert began.wait(timeout=10)
        response = revoke(client, auth, wf)
        assert response.status_code == 200
        proceed.set()
        future.result(timeout=15)
    assert not errors
    assert results[0]["status"] == "submission_unknown"
    assert owner_db.execute("SELECT count(*) AS n FROM simulator_ledger").fetchone()["n"] == 0


def test_revocation_spans_all_workflows_for_same_grant_and_preserves_audit(owner_db):
    client = TestClient(app)
    auth = login(client)
    first = create_workflow(client, auth)
    second_id = owner_db.execute(
        """INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version)
           VALUES ('borrower-001',%s,'consented',%s) RETURNING id""",
        (first["consent_grant_id"], POLICY_VERSION),
    ).fetchone()["id"]
    owner_db.commit()
    for workflow_id in (first["id"], str(second_id)):
        from lending_lab.db import append_audit_event
        with transaction() as conn:
            append_audit_event(conn, workflow_id, "test", "before-revocation", "consented", "consented",
                               {"purpose": "illustrative_offer_comparison"}, "test")
    response = revoke(client, auth, first)
    assert response.status_code == 200
    assert response.json()["affected_workflows"] == 2
    states = owner_db.execute("SELECT state FROM workflows WHERE consent_grant_id=%s ORDER BY id",
                              (first["consent_grant_id"],)).fetchall()
    assert [row["state"] for row in states] == ["consent_revoked", "consent_revoked"]
    for workflow_id in (first["id"], str(second_id)):
        assert owner_db.execute("SELECT verify_audit_chain(%s) AS ok", (workflow_id,)).fetchone()["ok"] is True
        assert owner_db.execute("SELECT action FROM audit_events WHERE workflow_id=%s ORDER BY sequence DESC LIMIT 1",
                                (workflow_id,)).fetchone()["action"] == "consent.revoked"


def test_regrant_requires_new_grant_workflow_and_explicit_synthetic_inputs(owner_db):
    client = TestClient(app)
    auth = login(client)
    original = create_workflow(client, auth)
    assert revoke(client, auth, original).status_code == 200
    missing = client.post("/workflows", headers=auth, json={"purpose": "illustrative_offer_comparison",
                          "consent_text": CONSENT_TEXT})
    assert missing.status_code == 409
    new = create_workflow(client, auth, {
        "monthly_income_minor": 390000, "monthly_debt_minor": 70000,
        "requested_amount_minor": 1800000, "term_months": 24,
        "employment_months": 40, "region": "synthetic-reentered",
    })
    assert new["id"] != original["id"]
    assert new["consent_grant_id"] != original["consent_grant_id"]
    assert client.post(f"/workflows/{new['id']}/evaluate", headers=auth).status_code == 200
    assert owner_db.execute("SELECT status FROM consent_grants WHERE id=%s",
                            (original["consent_grant_id"],)).fetchone()["status"] == "revoked"


def test_revocation_authorization_owner_and_operator_boundaries():
    client = TestClient(app)
    owner = login(client)
    other = login(client, "borrower-002")
    operator = login(client, "lab-operator")
    wf = create_workflow(client, owner)
    assert revoke(client, other, wf).status_code == 404
    assert client.post(f"/consents/{wf['consent_grant_id']}/revoke", headers=operator).status_code == 403
    assert revoke(client, owner, wf).status_code == 200
