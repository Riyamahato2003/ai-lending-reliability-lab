"""Separately provisioned, revocable identities for a synthetic-data pilot.

No self-signup, passwords or privileged account creation through the API.
All pilot credentials are created by a migration-owner-only CLI.
"""
import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from lending_lab.auth import Principal, _b64
from lending_lab.clock import system_clock
from lending_lab.config import get_settings
from lending_lab.credentials import hash_demo_password, verify_demo_password
from lending_lab.db import connect, transaction
from lending_lab.schemas import Role

AUDIENCE = "verity-pilot-v1"
LOGIN_LIMIT = 8
LOGIN_WINDOW_MINUTES = 15
_DUMMY_PASSWORD_DIGEST = hash_demo_password("unprovisioned", "invalid-pilot-password")


def _username(value: str) -> str:
    # Accounts are email-address handles and are always stored lowercased.
    return value.strip().lower()


def _allow_login_attempt(conn, username: str) -> bool:
    # One atomic upsert across API workers prevents independent rate-limit
    # counters on every instance. External IP-based throttling is still needed.
    key = hashlib.sha256(username.encode("utf-8")).hexdigest()
    row = conn.execute(
        """INSERT INTO pilot_login_limits(username_hash,attempts,window_started)
           VALUES (%s,1,now())
           ON CONFLICT (username_hash) DO UPDATE SET
             attempts=CASE WHEN pilot_login_limits.window_started
                           < now() - interval '15 minutes'
                           THEN 1 ELSE pilot_login_limits.attempts+1 END,
             window_started=CASE WHEN pilot_login_limits.window_started
                           < now() - interval '15 minutes'
                           THEN now() ELSE pilot_login_limits.window_started END
           RETURNING attempts""",
        (key,),
    ).fetchone()
    return row["attempts"] <= LOGIN_LIMIT


def issue_pilot_session(account) -> tuple[str, dict]:
    now = system_clock.unix_seconds()
    expires = now + get_settings().session_ttl_seconds
    claims = {
        "aud": AUDIENCE,
        "sub": str(account["id"]),
        "username": account["username"],
        "role": account["role"],
        "borrower_id": account["borrower_id"],
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": expires,
    }
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(
        get_settings().session_signing_secret.encode(), body.encode(), hashlib.sha256
    ).digest()
    return f"{body}.{_b64(signature)}", claims


def authenticate_pilot(username: str, password: str) -> tuple[str, dict]:
    if get_settings().app_mode != "production":
        raise HTTPException(status_code=404, detail="Pilot authentication is unavailable")
    username = _username(username)
    # Never log the username, password, or token.
    with transaction() as conn:
        allowed = _allow_login_attempt(conn, username)
        if not allowed:
            raise HTTPException(status_code=429, detail="Too many login attempts; try again later")
        account = conn.execute(
            """SELECT id,username,password_digest,role,borrower_id,is_active
               FROM pilot_accounts WHERE username=%s""", (username,)
        ).fetchone()
        stored = account["password_digest"] if account else _DUMMY_PASSWORD_DIGEST
        password_matches = verify_demo_password(username, password, stored)
        if account is None or not password_matches or not account["is_active"]:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token, claims = issue_pilot_session(account)
        conn.execute(
            """INSERT INTO pilot_sessions(session_id,account_id,expires_at)
               VALUES (%s,%s,to_timestamp(%s))""",
            (claims["jti"], account["id"], claims["exp"]),
        )
    return token, claims


def verify_pilot_session(token: str) -> Principal:
    if get_settings().app_mode != "production":
        raise HTTPException(status_code=401, detail="Pilot authentication is unavailable")
    try:
        if len(token) > 4096:
            raise ValueError("oversized token")
        body, supplied = token.split(".", 1)
        expected = _b64(hmac.new(
            get_settings().session_signing_secret.encode(), body.encode(), hashlib.sha256
        ).digest())
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("signature")
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if claims.get("aud") != AUDIENCE:
            raise ValueError("audience")
        if not isinstance(claims["exp"], int) or claims["exp"] <= system_clock.unix_seconds():
            raise ValueError("expired")
        if not isinstance(claims["iat"], int) or claims["iat"] > system_clock.unix_seconds() + 60:
            raise ValueError("invalid issue time")
        if claims["exp"] - claims["iat"] > get_settings().session_ttl_seconds:
            raise ValueError("invalid duration")
        account_id = str(uuid.UUID(claims["sub"]))
        session_id = str(uuid.UUID(claims["jti"]))
        role = Role(claims["role"])
        borrower_id = claims.get("borrower_id")
        username = claims["username"]
        if not isinstance(username, str) or not isinstance(borrower_id, (str, type(None))):
            raise ValueError("claims")
        if (role == Role.BORROWER) != (borrower_id is not None):
            raise ValueError("role binding")
        with connect() as conn:
            active = conn.execute(
                """SELECT 1
                   FROM pilot_sessions s JOIN pilot_accounts a ON a.id=s.account_id
                   WHERE s.session_id=%s AND a.id=%s
                     AND a.username=%s AND a.role=%s
                     AND a.borrower_id IS NOT DISTINCT FROM %s
                     AND a.is_active=true
                     AND s.revoked_at IS NULL AND s.expires_at>now()""",
                (session_id, account_id, username, role.value, borrower_id),
            ).fetchone()
        if not active:
            raise ValueError("inactive account or session")
        return Principal(username, role, borrower_id, session_id)
    except (ValueError, TypeError, KeyError, IndexError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired pilot session") from exc


def revoke_pilot_session(principal: Principal) -> None:
    with transaction() as conn:
        conn.execute(
            """UPDATE pilot_sessions SET revoked_at=now()
               WHERE session_id=%s AND revoked_at IS NULL""",
            (principal.session_id,),
        )
