import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from lending_lab.config import get_settings
from lending_lab.clock import Clock, system_clock
from lending_lab.db import append_security_event, connect
from lending_lab.schemas import Role


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def issue_session(username: str, role: Role, borrower_id: str | None,
                  clock: Clock = system_clock) -> tuple[str, dict[str, Any]]:
    now = clock.unix_seconds()
    session_id = str(uuid.uuid4())
    payload = {"sub": username, "role": role.value, "borrower_id": borrower_id,
               "jti": session_id, "iat": now, "exp": now + get_settings().session_ttl_seconds}
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(get_settings().session_signing_secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}", payload


@dataclass(frozen=True)
class Principal:
    username: str
    role: Role
    borrower_id: str | None
    session_id: str


def verify_session(token: str, clock: Clock = system_clock) -> Principal:
    try:
        body, supplied = token.split(".", 1)
        expected = _b64(hmac.new(get_settings().session_signing_secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("signature")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if int(payload["exp"]) <= clock.unix_seconds():
            raise ValueError("expired")
        role = Role(payload["role"])
        borrower_id = payload.get("borrower_id")
        if (role == Role.BORROWER) != (borrower_id is not None):
            raise ValueError("binding")
        with connect() as conn:
            active = conn.execute(
                """SELECT 1 FROM demo_sessions s JOIN demo_users u ON u.username=s.username
                   WHERE s.session_id=%s AND s.username=%s AND s.role=%s
                   AND s.borrower_id IS NOT DISTINCT FROM %s AND s.revoked_at IS NULL
                   AND s.expires_at > now() AND u.role=s.role
                   AND u.borrower_id IS NOT DISTINCT FROM s.borrower_id""",
                (payload["jti"], payload["sub"], role.value, borrower_id),
            ).fetchone()
        if not active:
            raise ValueError("session revoked or unknown")
        return Principal(payload["sub"], role, borrower_id, payload["jti"])
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired demo session") from exc


def get_principal(request: Request, authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        append_security_event("authentication_failure", request.state.correlation_id,
                              details={"reason": "missing_bearer_session"})
        raise HTTPException(status_code=401, detail="Bearer session required")
    try:
        return verify_session(authorization.split(" ", 1)[1])
    except HTTPException:
        append_security_event("authentication_failure", request.state.correlation_id,
                              details={"reason": "invalid_expired_or_revoked_session"})
        raise


def require_borrower(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != Role.BORROWER or principal.borrower_id is None:
        append_security_event("unauthorized_role_action", request.state.correlation_id,
                              actor_role=principal.role.value, actor_ref=principal.username,
                              resource_type="borrower_api", details={"reason": "borrower_role_required"})
        raise HTTPException(status_code=403, detail="Borrower role required")
    return principal


def require_operator(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != Role.OPERATOR:
        append_security_event("unauthorized_operator_action", request.state.correlation_id,
                              actor_role=principal.role.value, actor_ref=principal.username,
                              resource_type="operator_api", details={"reason": "operator_role_required"})
        raise HTTPException(status_code=403, detail="Operator role required")
    return principal
