import hashlib
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from lending_lab.api import app
from lending_lab.db import append_audit_event, append_security_event


def _login(client, username, password):
    response = client.post("/demo/sessions", json={"username": username, "password": password})
    return response


def _security_events(owner_db, event_type):
    return owner_db.execute("SELECT event_type,actor_role,actor_ref,resource_type,resource_id,details,correlation_id FROM security_events WHERE event_type=%s ORDER BY created_at", (event_type,)).fetchall()


def test_runtime_role_cannot_modify_events_or_remove_audit_triggers(db, owner_db):
    grant = db.execute("INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,status) VALUES ('borrower-001','test',%s,'active') RETURNING id",
                       (hashlib.sha256(b"synthetic").hexdigest(),)).fetchone()
    wf = db.execute("INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) VALUES ('borrower-001',%s,'consented','test-v1') RETURNING id", (grant["id"],)).fetchone()
    append_audit_event(db, wf["id"], "test", "created", None, "consented", {}, "test-correlation")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("UPDATE audit_events SET action='forged' WHERE workflow_id=%s", (wf["id"],))
    db.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("DROP TRIGGER audit_events_immutable ON audit_events")
    db.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_immutable")
    db.rollback()
    protected = {
        "audit_events": "audit_events_immutable",
        "security_events": "security_events_immutable",
        "evaluation_snapshots": "evaluation_snapshots_immutable",
        "simulator_ledger": "simulator_ledger_immutable",
    }
    for table, trigger_name in protected.items():
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(f"DROP TRIGGER {trigger_name} ON {table}")
        db.rollback()
        trigger = owner_db.execute(
            "SELECT tgenabled FROM pg_trigger WHERE tgrelid=%s::regclass AND tgname=%s AND NOT tgisinternal",
            (table, trigger_name),
        ).fetchone()
        enabled = trigger["tgenabled"].decode() if isinstance(trigger["tgenabled"], bytes) else trigger["tgenabled"]
        assert enabled == "O"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("UPDATE security_events SET details='{}'::jsonb")
    db.rollback()
    trigger = owner_db.execute("SELECT tgenabled FROM pg_trigger WHERE tgrelid='audit_events'::regclass AND tgname='audit_events_immutable' AND NOT tgisinternal").fetchone()
    assert trigger["tgenabled"] == "O"


def test_lab_database_role_cannot_read_or_modify_borrower_profiles(lab_db):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        lab_db.execute("SELECT input_data FROM sensitive_borrower_data")
    lab_db.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        lab_db.execute("UPDATE sensitive_borrower_data SET input_data='{}'::jsonb WHERE borrower_id='borrower-001'")
    lab_db.rollback()


def test_auth_failure_is_logged_without_credentials(db, owner_db):
    client = TestClient(app)
    response = _login(client, "borrower-001", "definitely-not-the-password")
    assert response.status_code == 401
    event = _security_events(owner_db, "authentication_failure")[-1]
    assert event["details"] == {"reason": "invalid_demo_credentials"}
    assert "password" not in str(event).lower()
    assert "definitely-not-the-password" not in str(event)


def test_security_event_log_is_append_only(db, owner_db):
    append_security_event("authentication_failure", "security-test-correlation",
                          details={"reason": "test"})
    db.commit()
    event = owner_db.execute("SELECT id FROM security_events WHERE correlation_id='security-test-correlation'").fetchone()
    with pytest.raises(psycopg.Error, match="append-only"):
        owner_db.execute("UPDATE security_events SET event_type='unauthorized_role_action' WHERE id=%s", (event["id"],))
    owner_db.rollback()


def test_cross_borrower_denial_is_logged(db, owner_db):
    client = TestClient(app)
    one = _login(client, "borrower-001", "borrower-demo").json()["access_token"]
    two = _login(client, "borrower-002", "borrower-demo").json()["access_token"]
    created = client.post("/workflows", headers={"Authorization": f"Bearer {one}"}, json={
        "purpose": "illustrative_offer_comparison",
        "consent_text": "I consent to synthetic illustrative lender comparison for this demo.",
        "financial_details": {"monthly_income_minor": 420000, "monthly_debt_minor": 80000,
                              "requested_amount_minor": 2400000, "term_months": 24,
                              "employment_months": 36, "region": "synthetic-west"},
    }).json()
    response = client.get(f"/workflows/{created['id']}", headers={"Authorization": f"Bearer {two}"})
    assert response.status_code == 404
    assert client.post(f"/workflows/{created['id']}/evaluate", headers={"Authorization": f"Bearer {two}"}).status_code == 404
    assert client.post(f"/workflows/{created['id']}/transition", headers={"Authorization": f"Bearer {two}"},
                       json={"target": "cancelled", "expected_version": 0}).status_code == 404
    event = _security_events(owner_db, "cross_borrower_access_denial")[-1]
    assert event["resource_id"] == created["id"]
    assert event["actor_role"] == "borrower"


def test_wrong_role_operator_action_is_logged(db, owner_db):
    client = TestClient(app)
    token = _login(client, "borrower-001", "borrower-demo").json()["access_token"]
    response = client.get("/operator/lab/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    event = _security_events(owner_db, "unauthorized_operator_action")[-1]
    assert event["actor_ref"] == "borrower-001"
    assert event["details"] == {"reason": "operator_role_required"}


def test_operator_lab_activity_is_logged_and_separate_from_workflow_audit(db, owner_db):
    client = TestClient(app)
    token = _login(client, "lab-operator", "operator-demo").json()["access_token"]
    response = client.get("/operator/lab/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    event = _security_events(owner_db, "operator_lab_administration")[-1]
    assert event["details"] == {"action": "status_view"}
    assert event["resource_type"] == "lab"


def test_logout_revokes_session(db, owner_db):
    client = TestClient(app)
    token = _login(client, "borrower-001", "borrower-demo").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/demo/sessions/logout", headers=headers).status_code == 204
    assert client.get("/workflows/00000000-0000-0000-0000-000000000001", headers=headers).status_code == 401
    assert _security_events(owner_db, "authentication_failure")[-1]["details"]["reason"] == "invalid_expired_or_revoked_session"


def test_server_side_user_role_change_invalidates_old_session(db, owner_db):
    client = TestClient(app)
    token = _login(client, "borrower-001", "borrower-demo").json()["access_token"]
    owner_db.execute("UPDATE demo_users SET role='operator',borrower_id=NULL WHERE username='borrower-001'")
    owner_db.commit()
    response = client.get("/workflows/00000000-0000-0000-0000-000000000001",
                          headers={"Authorization": f"Bearer {token}"})
    owner_db.execute("UPDATE demo_users SET role='borrower',borrower_id='borrower-001' WHERE username='borrower-001'")
    owner_db.commit()
    assert response.status_code == 401


def test_operator_token_cannot_use_borrower_routes(db, owner_db):
    client = TestClient(app)
    borrower_token = _login(client, "borrower-001", "borrower-demo").json()["access_token"]
    created = client.post("/workflows", headers={"Authorization": f"Bearer {borrower_token}"}, json={
        "purpose": "illustrative_offer_comparison",
        "consent_text": "I consent to synthetic illustrative lender comparison for this demo.",
        "financial_details": {"monthly_income_minor": 420000, "monthly_debt_minor": 80000,
                              "requested_amount_minor": 2400000, "term_months": 24,
                              "employment_months": 36, "region": "synthetic-west"},
    }).json()
    token = _login(client, "lab-operator", "operator-demo").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/workflows", headers=headers, json={
        "purpose": "illustrative_offer_comparison",
        "consent_text": "I consent to synthetic illustrative lender comparison for this demo.",
        "financial_details": {"monthly_income_minor": 420000, "monthly_debt_minor": 80000,
                              "requested_amount_minor": 2400000, "term_months": 24,
                              "employment_months": 36, "region": "synthetic-west"},
    })
    assert response.status_code == 403
    assert client.get(f"/workflows/{created['id']}", headers=headers).status_code == 403
    assert client.post(f"/workflows/{created['id']}/evaluate", headers=headers).status_code == 403
    assert client.post(f"/workflows/{created['id']}/transition", headers=headers,
                       json={"target": "cancelled", "expected_version": created["version"]}).status_code == 403
    assert _security_events(owner_db, "unauthorized_role_action")[-1]["details"] == {"reason": "borrower_role_required"}
