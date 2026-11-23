import os

import pytest

# The test suite is explicitly a local synthetic demo; production startup tests opt out.
os.environ.setdefault("APP_MODE", "local_demo")


@pytest.fixture(scope="session")
def postgres_database():
    runtime_url = os.environ.get("TEST_DATABASE_URL")
    migration_url = os.environ.get("TEST_MIGRATION_DATABASE_URL")
    lab_url = os.environ.get("TEST_LAB_DATABASE_URL")
    if not all((runtime_url, migration_url, lab_url)):
        if os.environ.get("REQUIRE_POSTGRES_TESTS", "").lower() in {"1", "true", "yes"}:
            pytest.fail("CI requires TEST_DATABASE_URL, TEST_MIGRATION_DATABASE_URL, and TEST_LAB_DATABASE_URL")
        pytest.skip("Set TEST_DATABASE_URL, TEST_MIGRATION_DATABASE_URL, and TEST_LAB_DATABASE_URL to disposable PostgreSQL roles")
    os.environ["DATABASE_URL"] = runtime_url
    os.environ["MIGRATION_DATABASE_URL"] = migration_url
    os.environ["LAB_DATABASE_URL"] = lab_url
    from lending_lab.config import get_settings
    get_settings.cache_clear()
    from lending_lab.migrate import migrate
    migrate()
    from lending_lab.db import connect_migrator
    with connect_migrator() as conn:
        conn.execute("TRUNCATE security_events,operator_resolution_events,sensitive_approval_data,sensitive_dispatch_payloads,sensitive_lender_result_data,sensitive_evaluation_snapshot_data,sensitive_borrower_data,submission_intents,approvals,demo_sessions,lab_events,lab_runs,audit_events,workflow_steps,lender_results,evaluation_snapshots,workflows,consent_grants,simulator_ledger,demo_users,borrower_profiles CASCADE")
        conn.execute("INSERT INTO borrower_profiles(id) VALUES ('borrower-001'),('borrower-002')")
        conn.execute("""INSERT INTO sensitive_borrower_data(borrower_id,input_data) VALUES
          ('borrower-001','{"monthly_income_minor":420000,"monthly_debt_minor":80000,"requested_amount_minor":2400000,"term_months":24,"employment_months":36,"region":"synthetic-west"}'),
          ('borrower-002','{"monthly_income_minor":230000,"monthly_debt_minor":120000,"requested_amount_minor":3900000,"term_months":36,"employment_months":8,"region":"synthetic-east"}')""")
    from lending_lab.config import get_settings
    from lending_lab.credentials import hash_demo_password
    s = get_settings()
    with connect_migrator() as conn:
        conn.execute("""INSERT INTO demo_users(username,password_digest,role,borrower_id) VALUES
          ('borrower-001',%s,'borrower','borrower-001'),('borrower-002',%s,'borrower','borrower-002'),
          ('lab-operator',%s,'operator',NULL)""",
                     (hash_demo_password("borrower-001", s.demo_borrower_password),
                      hash_demo_password("borrower-002", s.demo_borrower_password),
                      hash_demo_password("lab-operator", s.demo_operator_password)))


@pytest.fixture
def db(postgres_database):
    from lending_lab.db import connect
    conn = connect()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def owner_db(postgres_database):
    from lending_lab.db import connect_migrator
    conn = connect_migrator()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def lab_db(postgres_database):
    from lending_lab.db import connect_lab
    conn = connect_lab()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
