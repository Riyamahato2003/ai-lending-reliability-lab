"""Create isolated runtime/lab roles in a disposable PostgreSQL test database."""
import os

import psycopg
from psycopg import sql


def main() -> None:
    admin_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
    runtime_password = os.environ.get("TEST_RUNTIME_PASSWORD", "runtime-test-password")
    lab_password = os.environ.get("TEST_LAB_PASSWORD", "lab-test-password")
    with psycopg.connect(admin_url) as conn:
        conn.execute(sql.SQL("CREATE ROLE lending_runtime LOGIN PASSWORD {}").format(sql.Literal(runtime_password)))
        conn.execute(sql.SQL("CREATE ROLE lending_lab_operator LOGIN PASSWORD {}").format(sql.Literal(lab_password)))
        conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


if __name__ == "__main__":
    main()
