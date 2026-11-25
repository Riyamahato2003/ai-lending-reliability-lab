from pathlib import Path

from lending_lab.config import get_settings
from lending_lab.db import connect_migrator
from lending_lab.credentials import hash_demo_password


def migrate() -> None:
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    with connect_migrator() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.stem
            already = conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (version,)).fetchone() if _table_exists(conn) else None
            if already:
                continue
            conn.execute(path.read_text())
        # Preserve schema migrations in all modes but NEVER populate fixed demo
        # identities in non-local environments. Existing demo rows are also
        # unusable outside local mode because auth verifies APP_MODE.
        if get_settings().app_mode != "local_demo":
            return
        # These synthetic credentials are local demo credentials, never production identities.
        borrower_password = hash_demo_password("borrower-001", get_settings().demo_borrower_password)
        borrower_2_password = hash_demo_password("borrower-002", get_settings().demo_borrower_password)
        operator_password = hash_demo_password("lab-operator", get_settings().demo_operator_password)
        conn.execute("""INSERT INTO demo_users(username,password_digest,role,borrower_id) VALUES
          ('borrower-001',%s,'borrower','borrower-001'),('borrower-002',%s,'borrower','borrower-002'),
          ('lab-operator',%s,'operator',NULL)
          ON CONFLICT(username) DO UPDATE SET password_digest=excluded.password_digest""",
                     (borrower_password, borrower_2_password, operator_password))


def _table_exists(conn) -> bool:
    return conn.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL AS exists").fetchone()["exists"]


if __name__ == "__main__":
    migrate()
