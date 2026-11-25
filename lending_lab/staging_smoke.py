"""One-shot synthetic pilot smoke test inside the private Docker network.

Requires STAGING_SMOKE_ENABLE=1. No real borrower data, emails, or lenders.
Never print generated credentials or tokens.
"""
import json
import os
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from lending_lab.config import get_settings
from lending_lab.pilot_admin import create_account, disable_account

BASE = "http://api:8000"


def check(path: str, *, method: str = "GET", payload: dict | None = None,
          token: str | None = None, expected: int = 200):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            code, raw = response.status, response.read()
    except HTTPError as error:
        code, raw = error.code, error.read()
    if code != expected:
        raise AssertionError(f"{method} {path}: expected HTTP {expected}, received {code}")
    return json.loads(raw) if raw else None


def main() -> None:
    if os.getenv("STAGING_SMOKE_ENABLE") != "1":
        raise SystemExit("Refusing to provision a staging smoke account without explicit opt-in")
    settings = get_settings()
    settings.validate_startup()
    if settings.app_mode != "production" or settings.ai_mode != "deterministic":
        raise SystemExit("Smoke test requires production-mode synthetic deterministic staging")

    health = check("/health")
    if health.get("database") != "connected":
        raise AssertionError("Staging database is not healthy")
    if check("/auth/capabilities") != {"login_mode": "pilot"}:
        raise AssertionError("Demo capabilities unexpectedly enabled")

    # Never allow seeded demo accounts to authenticate in pilot mode.
    check("/demo/sessions", method="POST", payload={
        "username": "borrower-001", "password": "borrower-demo"
    }, expected=404)

    name = f"ci-smoke-{uuid.uuid4().hex[:16]}@example.test"
    unknown = f"unknown-{uuid.uuid4().hex[:16]}@example.test"
    secret = create_account(name, "borrower")
    try:
        check("/pilot/sessions", method="POST",
              payload={"username": unknown, "password": "invalid"}, expected=401)
        auth = check("/pilot/sessions", method="POST",
                     payload={"username": name, "password": secret})
        if auth["role"] != "borrower" or not auth["borrower_id"].startswith("pilot-"):
            raise AssertionError("Unexpected pilot role or borrower binding")
        token = auth["access_token"]

        # Existing protected borrower endpoints must work with an individual identity.
        if check("/workflows", token=token) != []:
            raise AssertionError("Fresh pilot borrower unexpectedly has existing workflows")
        check("/operator/lab/runs", token=token, expected=403)
        check("/pilot/sessions/logout", method="POST", token=token, expected=204)
        check("/workflows", token=token, expected=401)
        print("PASS: private staging database, pilot auth, role boundary, logout and demo denial")
    finally:
        # Do not leave the disposable smoke account active.
        disable_account(name)


if __name__ == "__main__":
    main()
