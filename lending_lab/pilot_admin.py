"""Restricted operator CLI for separately provisioned synthetic pilot accounts.

Run only in a trusted terminal using the migration-owner database credential.
No account-management API is intentionally exposed to application users.
"""
import argparse
import re
import secrets
import uuid

from lending_lab.config import get_settings
from lending_lab.credentials import hash_demo_password
from lending_lab.db import connect_migrator

EMAIL = re.compile(r"^[a-z0-9][a-z0-9._+%-]{0,62}@[a-z0-9.-]+\.[a-z]{2,}$")


def normalized_username(raw: str) -> str:
    name = raw.strip().lower()
    if len(name) > 80 or not EMAIL.fullmatch(name):
        raise ValueError("Pilot username must be a valid email address (max 80 characters)")
    return name


def create_account(username: str, role: str) -> str:
    username = normalized_username(username)
    if role not in ("borrower", "operator"):
        raise ValueError("role must be borrower or operator")
    password = secrets.token_urlsafe(32)
    digest = hash_demo_password(username, password)
    borrower_id = f"pilot-{uuid.uuid4().hex}" if role == "borrower" else None
    with connect_migrator() as conn:
        if borrower_id:
            conn.execute("INSERT INTO borrower_profiles(id) VALUES (%s)", (borrower_id,))
        conn.execute(
            """INSERT INTO pilot_accounts(username,password_digest,role,borrower_id)
               VALUES (%s,%s,%s,%s)""",
            (username, digest, role, borrower_id),
        )
    return password


def disable_account(username: str) -> bool:
    username = normalized_username(username)
    with connect_migrator() as conn:
        row = conn.execute(
            "UPDATE pilot_accounts SET is_active=false,updated_at=now() WHERE username=%s RETURNING id",
            (username,),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE pilot_sessions SET revoked_at=now()
                   WHERE account_id=%s AND revoked_at IS NULL""",
                (row["id"],),
            )
    return row is not None


def reset_password(username: str) -> str | None:
    username = normalized_username(username)
    password = secrets.token_urlsafe(32)
    digest = hash_demo_password(username, password)
    with connect_migrator() as conn:
        row = conn.execute(
            """UPDATE pilot_accounts SET password_digest=%s,updated_at=now()
               WHERE username=%s AND is_active=true RETURNING id""",
            (digest, username),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE pilot_sessions SET revoked_at=now()
                   WHERE account_id=%s AND revoked_at IS NULL""",
                (row["id"],),
            )
    return password if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage invited Verity pilot users")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=("borrower", "operator"), default="borrower")
    disable = sub.add_parser("disable")
    disable.add_argument("--username", required=True)
    reset = sub.add_parser("reset-password")
    reset.add_argument("--username", required=True)
    args = parser.parse_args()

    settings = get_settings()
    settings.validate_startup()
    if settings.app_mode != "production":
        parser.error("Pilot account administration requires APP_MODE=production")

    if args.command == "create":
        generated = create_account(args.username, args.role)
        print(f"Created {args.role} account for {normalized_username(args.username)}.")
        print(f"One-time password: {generated}")
        print("Provide privately through an approved secure channel. Do not copy into logs or Git.")
    elif args.command == "disable":
        print("Account disabled and sessions revoked." if disable_account(args.username)
              else "No matching account.")
    else:
        generated = reset_password(args.username)
        if not generated:
            print("No active account matched.")
        else:
            print(f"One-time replacement password: {generated}")
            print("All previous sessions were revoked. Share password privately.")


if __name__ == "__main__":
    main()
