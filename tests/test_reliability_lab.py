"""Operator-only, isolated Reliability Lab experiments on real PostgreSQL."""

from copy import deepcopy

import psycopg
import pytest
from fastapi.testclient import TestClient

from lending_lab.api import app
from lending_lab.db import connect_migrator, connect_lab
from lending_lab.schemas import LabFaultScenario

pytestmark = pytest.mark.usefixtures("postgres_database")
CONSENT_TEXT = "I consent to synthetic illustrative lender comparison for this demo."
SYNTHETIC_DETAILS = {
    "monthly_income_minor": 420000,
    "monthly_debt_minor": 80000,
    "requested_amount_minor": 2400000,
    "term_months": 24,
    "employment_months": 36,
    "region": "synthetic-west",
}


@pytest.fixture(autouse=True)
def reset_lab_tables(postgres_database):
    with connect_migrator() as conn:
        conn.execute("TRUNCATE lab_runs CASCADE")


def login(client, username="lab-operator"):
    password = "operator-demo" if username == "lab-operator" else "borrower-demo"
    response = client.post("/demo/sessions", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def run(client, auth, implementation, scenario, seed=12345, trials=3, revoke=False):
    response = client.post("/operator/lab/runs", headers=auth, json={
        "implementation": implementation,
        "scenario": scenario,
        "seed": seed,
        "trials": trials,
        "revoke_before_recovery": revoke,
    })
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("scenario", [item.value for item in LabFaultScenario])
def test_each_fault_scenario_compares_safe_and_unsafe_on_same_schedule(scenario):
    client = TestClient(app)
    auth = login(client)
    safe = run(client, auth, "safe", scenario, seed=91, trials=2)
    unsafe = run(client, auth, "unsafe", scenario, seed=91, trials=2)
    assert safe["configuration"]["scenario"] == unsafe["configuration"]["scenario"] == scenario
    assert safe["seed"] == unsafe["seed"] == 91
    assert safe["trials"] == unsafe["trials"] == 2
    assert safe["deterministic_schedule"] and unsafe["deterministic_schedule"]
    assert safe["run_status"] == unsafe["run_status"] == "completed"
    assert all(count == 0 for count in safe["invariant_violations"].values())
    assert all(value["denominator_trials"] == 2 and
               value["numerator_violating_trials"] == safe["violation_trials"][name]
               for name, value in safe["metrics"]["invariant_violation_rates"].items())
    comparison = client.post("/operator/lab/compare", headers=auth, json={
        "safe_run_id": safe["id"], "unsafe_run_id": unsafe["id"],
    })
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()["schedule_hash"]

    if scenario == "EFFECT_COMMITTED_RESPONSE_LOST":
        assert safe["effects_created"] == safe["confirmed_effects"] == 2
        assert unsafe["confirmed_effects"] == 0
        assert unsafe["workflows_marked_submitted"] == 2
        assert safe["attempt_count"] == 2
        assert safe["recovered_workflows"] == 2
        assert unsafe["effects_created"] == 4
        assert unsafe["duplicate_effects"] == 2
    elif scenario == "DUPLICATE_RESPONSE":
        assert safe["effects_created"] == unsafe["effects_created"] == 2
        assert safe["duplicate_response_deliveries"] == unsafe["duplicate_response_deliveries"] == 2
        assert unsafe["invariant_violations"]["no_confirmation_without_authoritative_evidence"] == 2
    elif scenario == "CONFLICTING_RESPONSE":
        assert safe["recovered_workflows"] == 2
        assert unsafe["invariant_violations"]["immutable_approval_and_dispatch_bindings"] == 2
    elif scenario == "RESPONSE_AFTER_DEADLINE":
        assert safe["late_responses"] == unsafe["late_responses"] == 2
        assert safe["recovered_workflows"] == 2
        assert unsafe["invariant_violations"]["no_confirmation_without_authoritative_evidence"] == 2
    else:
        assert safe["effects_created"] == unsafe["effects_created"] == 2
        assert safe["recovered_workflows"] == 2
        assert unsafe["invariant_violations"]["uncertain_outcome_not_reported_as_rejection"] == 2

    fetched = client.get(f"/operator/lab/runs/{safe['id']}", headers=auth)
    assert fetched.status_code == 200
    assert len(fetched.json()["trial_outcomes"]) == 2
    with connect_lab() as conn:
        intent_count = conn.execute("SELECT count(*) AS n FROM lab_dispatch_intents WHERE run_id=%s",
                                    (safe["id"],)).fetchone()["n"]
        unsafe_intent_count = conn.execute("SELECT count(*) AS n FROM lab_dispatch_intents WHERE run_id=%s",
                                           (unsafe["id"],)).fetchone()["n"]
        effect_count = conn.execute("SELECT count(*) AS n FROM lab_effects WHERE run_id=%s",
                                    (safe["id"],)).fetchone()["n"]
    assert intent_count == safe["trials"]
    assert unsafe_intent_count == 0
    assert effect_count == safe["effects_created"]


def test_revocation_fault_interactions_never_resend_safely_and_show_unsafe_violation():
    client = TestClient(app)
    auth = login(client)
    safe_lost = run(client, auth, "safe", "REQUEST_LOST_BEFORE_LENDER", seed=33, trials=2, revoke=True)
    unsafe_lost = run(client, auth, "unsafe", "REQUEST_LOST_BEFORE_LENDER", seed=33, trials=2, revoke=True)
    assert safe_lost["effects_created"] == 0
    assert safe_lost["attempt_count"] == 2  # one lost provider attempt per trial; blocked retries add none
    assert safe_lost["unresolved_workflows"] == 2
    assert safe_lost["unauthorized_effects"] == 0
    assert unsafe_lost["unauthorized_effects"] == 2
    assert unsafe_lost["invariant_violations"]["no_effect_after_revocation_commit"] == 2

    safe_effect = run(client, auth, "safe", "EFFECT_COMMITTED_RESPONSE_LOST", seed=34, trials=2, revoke=True)
    unsafe_effect = run(client, auth, "unsafe", "EFFECT_COMMITTED_RESPONSE_LOST", seed=34, trials=2, revoke=True)
    assert safe_effect["effects_created"] == safe_effect["recovered_workflows"] == 2
    assert safe_effect["unauthorized_effects"] == 0
    assert unsafe_effect["effects_created"] == 4
    assert unsafe_effect["unauthorized_effects"] == 2


def test_seeded_replay_has_same_logical_results_and_metadata():
    client = TestClient(app)
    auth = login(client)
    first = run(client, auth, "safe", "CONFLICTING_RESPONSE", seed=773, trials=5)
    second = run(client, auth, "safe", "CONFLICTING_RESPONSE", seed=773, trials=5)
    assert first["configuration_hash"] == second["configuration_hash"]
    for field in ("attempt_count", "effects_created", "confirmed_effects", "workflows_marked_submitted", "duplicate_effects",
                  "unauthorized_effects", "unverified_confirmations", "recovered_workflows",
                  "unresolved_workflows", "recovery_steps", "invariant_violations", "violation_trials"):
        assert first[field] == second[field]
    def logical_trials(record):
        outcomes = deepcopy(record["trial_outcomes"])
        for outcome in outcomes:
            outcome.pop("recovery_duration_ms")
        return outcomes
    assert logical_trials(client.get(f"/operator/lab/runs/{first['id']}", headers=auth).json()) == \
           logical_trials(client.get(f"/operator/lab/runs/{second['id']}", headers=auth).json())
    with connect_lab() as conn:
        first_keys = conn.execute("SELECT idempotency_key FROM lab_simulator_ledger WHERE run_id=%s ORDER BY trial_index",
                                  (first["id"],)).fetchall()
        second_keys = conn.execute("SELECT idempotency_key FROM lab_simulator_ledger WHERE run_id=%s ORDER BY trial_index",
                                   (second["id"],)).fetchall()
    assert [row["idempotency_key"] for row in first_keys] == [row["idempotency_key"] for row in second_keys]
    assert first["commit_sha"].startswith(("uncommitted:", "unknown")) or len(first["commit_sha"]) == 40
    assert first["environment"]["postgresql"]


def test_invalid_experiment_configurations_are_rejected_before_persistence():
    client = TestClient(app)
    auth = login(client)
    base = {"implementation": "safe", "scenario": "DUPLICATE_RESPONSE", "seed": 1, "trials": 2}
    invalid = [
        {**base, "scenario": "UNKNOWN_FAULT"},
        {**base, "trials": 101},
        {**base, "seed": -1},
        {**base, "revoke_before_recovery": True},
        {**base, "unexpected": "value"},
    ]
    for body in invalid:
        assert client.post("/operator/lab/runs", headers=auth, json=body).status_code == 422
    oversized = client.post("/operator/lab/runs", headers={**auth, "content-type": "application/json"},
                            content=b"{" + b" " * 20_000 + b"}")
    assert oversized.status_code == 413
    with connect_lab() as conn:
        assert conn.execute("SELECT count(*) AS n FROM lab_runs").fetchone()["n"] == 0


def test_operator_authorization_and_lab_role_database_isolation(db, lab_db):
    client = TestClient(app)
    borrower = login(client, "borrower-001")
    assert client.get("/operator/lab/scenarios", headers=borrower).status_code == 403
    assert client.post("/operator/lab/runs", headers=borrower, json={
        "implementation": "safe", "scenario": "REQUEST_LOST_BEFORE_LENDER", "seed": 1, "trials": 1,
    }).status_code == 403

    protected_tables = (
        "borrower_profiles", "workflows", "consent_grants", "evaluation_snapshots",
        "lender_results", "approvals", "submission_intents", "simulator_ledger",
    )
    for table in protected_tables:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            lab_db.execute(f"SELECT * FROM {table} LIMIT 1")
        lab_db.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            lab_db.execute(f"DELETE FROM {table} WHERE false")
        lab_db.rollback()
    for table in ("lab_runs", "lab_events", "lab_dispatch_intents", "lab_simulator_ledger", "lab_effects"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(f"SELECT * FROM {table} LIMIT 1")
        db.rollback()

    # Explicit lab-role privilege catalog check covers payload-bearing tables that
    # are intentionally absent from the lab role's search/authorization surface.
    with connect_lab() as conn:
        for table in (
            "borrower_profiles", "sensitive_borrower_data", "evaluation_snapshots",
            "sensitive_evaluation_snapshot_data", "consent_grants", "workflows",
            "submission_intents", "sensitive_dispatch_payloads", "simulator_ledger",
        ):
            assert conn.execute(
                "SELECT NOT has_table_privilege(current_user,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS isolated",
                (table,),
            ).fetchone()["isolated"] is True

    operator = login(client)
    record = run(client, operator, "safe", "REQUEST_LOST_BEFORE_LENDER", seed=7, trials=1)
    event_id = lab_db.execute("SELECT id FROM lab_events WHERE run_id=%s", (record["id"],)).fetchone()["id"]
    for statement, params in (
        ("UPDATE lab_runs SET seed=seed+1 WHERE id=%s", (record["id"],)),
        ("UPDATE lab_events SET case_name='changed' WHERE id=%s", (event_id,)),
        ("DELETE FROM lab_dispatch_intents WHERE run_id=%s", (record["id"],)),
        ("DELETE FROM lab_simulator_ledger WHERE run_id=%s", (record["id"],)),
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            lab_db.execute(statement, params)
        lab_db.rollback()


def test_lab_records_contain_no_borrower_payload_values():
    client = TestClient(app)
    auth = login(client)
    run(client, auth, "safe", "EFFECT_COMMITTED_RESPONSE_LOST", seed=3, trials=1)
    with connect_lab() as conn:
        records = conn.execute("""SELECT r.environment::text,r.configuration::text,
          r.metrics::text,e.outcome::text FROM lab_runs r JOIN lab_events e ON e.run_id=r.id""").fetchone()
    serialized = " ".join(records.values())
    for value in ("borrower-001", "monthly_income_minor", "monthly_debt_minor",
                  "requested_amount_minor", str(SYNTHETIC_DETAILS["monthly_income_minor"])):
        assert value not in serialized
