"""Integration tests for production-mode pilot auth, using synthetic PostgreSQL data."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from lending_lab.api import app
from lending_lab.auth import issue_session
from lending_lab.pilot_admin import create_account, disable_account, reset_password
from lending_lab.schemas import Role


@pytest.fixture
def client_in_pilot_mode(monkeypatch, db):
    """Production auth routes with disposable test DB and distinct test secrets."""
    settings = SimpleNamespace(
        app_mode="production",
        session_signing_secret="pilot-auth-test-signing-secret-very-long-and-randomized-for-tests",
        session_ttl_seconds=1800,
    )
    monkeypatch.setattr("lending_lab.api.get_settings", lambda: settings)
    monkeypatch.setattr("lending_lab.auth.get_settings", lambda: settings)
    monkeypatch.setattr("lending_lab.pilot_auth.get_settings", lambda: settings)
    return TestClient(app)


def unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.test"


def log_in(client, username: str, password: str):
    return client.post("/pilot/sessions", json={"username": username, "password": password})


def test_pilot_login_and_logout_revoke_session(client_in_pilot_mode):
    client = client_in_pilot_mode
    email = unique_email("pilot")
    password = create_account(email, "borrower")
    assert len(password) >= 32

    assert client.get("/auth/capabilities").json() == {"login_mode": "pilot"}
    signed = log_in(client, email, password)
    assert signed.status_code == 200, signed.text
    token = signed.json()["access_token"]
    assert signed.json()["role"] == "borrower"
    assert signed.json()["borrower_id"].startswith("pilot-")
    headers = {"Authorization": f"Bearer {token}"}

    # The existing API is the same protected borrower surface.
    assert client.get("/workflows", headers=headers).status_code == 200
    assert client.post("/pilot/sessions/logout", headers=headers).status_code == 204
    assert client.get("/workflows", headers=headers).status_code == 401


def test_individual_accounts_have_distinct_borrower_bindings(client_in_pilot_mode):
    client = client_in_pilot_mode
    one, two = unique_email("one"), unique_email("two")
    p1, p2 = create_account(one, "borrower"), create_account(two, "borrower")
    first = log_in(client, one, p1)
    second = log_in(client, two, p2)
    assert first.status_code == second.status_code == 200
    assert first.json()["borrower_id"] != second.json()["borrower_id"]
    assert client.get("/workflows", headers={
        "Authorization": "Bearer " + first.json()["access_token"]
    }).json() == []
    assert client.get("/workflows", headers={
        "Authorization": "Bearer " + second.json()["access_token"]
    }).json() == []


def test_pilot_users_cannot_read_each_others_workflows(client_in_pilot_mode):
    client = client_in_pilot_mode
    first_email, second_email = unique_email("owner"), unique_email("other")
    first_password = create_account(first_email, "borrower")
    second_password = create_account(second_email, "borrower")
    first_login = log_in(client, first_email, first_password)
    second_login = log_in(client, second_email, second_password)
    assert first_login.status_code == second_login.status_code == 200
    one = {"Authorization": "Bearer " + first_login.json()["access_token"]}
    two = {"Authorization": "Bearer " + second_login.json()["access_token"]}
    purpose = "illustrative_offer_comparison"
    consent = client.get("/consent-text/" + purpose, headers=one)
    assert consent.status_code == 200
    created = client.post("/workflows", headers=one, json={
        "purpose": purpose,
        "consent_text": consent.json()["consent_text"],
        "financial_details": {
            "monthly_income_minor": 420000,
            "monthly_debt_minor": 80000,
            "requested_amount_minor": 2400000,
            "term_months": 24,
            "employment_months": 36,
            "region": "synthetic-west"
        }
    })
    assert created.status_code == 200, created.text
    workflow_id = created.json()["id"]
    assert client.get("/workflows/" + workflow_id, headers=one).status_code == 200
    assert client.get("/workflows/" + workflow_id, headers=two).status_code == 404


def test_operator_does_not_inherit_borrower_privileges(client_in_pilot_mode):
    client = client_in_pilot_mode
    email = unique_email("operator")
    password = create_account(email, "operator")
    response = log_in(client, email, password)
    assert response.status_code == 200
    assert response.json()["borrower_id"] is None
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert client.get("/workflows", headers=headers).status_code == 403


def test_disable_user_immediately_invalidates_active_sessions(client_in_pilot_mode):
    client = client_in_pilot_mode
    email = unique_email("disable")
    password = create_account(email, "borrower")
    signed = log_in(client, email, password)
    assert signed.status_code == 200
    token = signed.json()["access_token"]
    assert disable_account(email) is True
    assert client.get("/workflows", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert log_in(client, email, password).status_code == 401


def test_reset_password_revokes_sessions(client_in_pilot_mode):
    client = client_in_pilot_mode
    email = unique_email("reset")
    original = create_account(email, "borrower")
    signed = log_in(client, email, original)
    assert signed.status_code == 200
    replacement = reset_password(email)
    assert replacement and replacement != original
    assert client.get("/workflows", headers={
        "Authorization": "Bearer " + signed.json()["access_token"]
    }).status_code == 401
    assert log_in(client, email, original).status_code == 401
    assert log_in(client, email, replacement).status_code == 200


def test_wrong_password_attempts_are_committed_and_throttled(client_in_pilot_mode):
    client = client_in_pilot_mode
    email = unique_email("throttle")
    create_account(email, "borrower")
    for _ in range(8):
        assert log_in(client, email, "incorrect").status_code == 401
    assert log_in(client, email, "incorrect").status_code == 429


def test_missing_account_and_demo_token_cannot_enter_pilot(client_in_pilot_mode):
    client = client_in_pilot_mode
    assert log_in(client, unique_email("unknown"), "incorrect").status_code == 401
    demo_token, _ = issue_session("borrower-001", Role.BORROWER, "borrower-001")
    assert client.get("/workflows", headers={
        "Authorization": "Bearer " + demo_token
    }).status_code == 401
    assert client.post("/pilot/accounts", json={"username": unique_email("self")}).status_code == 404


def test_pilot_account_cannot_be_created_with_runtime_db_role(db):
    import psycopg
    from lending_lab.credentials import hash_demo_password

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute(
            """INSERT INTO pilot_accounts(username,password_digest,role,borrower_id)
               VALUES (%s,%s,'operator',NULL)""",
            (unique_email("forbidden"), hash_demo_password("x", "pw")),
        )


def test_provisioning_validates_email_and_role():
    from lending_lab.pilot_admin import normalized_username
    assert normalized_username(" TEST+Alias@Example.COM ") == "test+alias@example.com"
    with pytest.raises(ValueError):
        normalized_username("no-at-sign")
    with pytest.raises(ValueError):
        normalized_username("bad\\example.com")
