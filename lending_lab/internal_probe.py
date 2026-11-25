"""One-shot, no-credential health probe for private synthetic Railway staging.

Raise on invalid responses so the Railway cron job is visibly failed.
This code never logs borrower data, passwords or access tokens.
"""
import json
import os
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def run() -> None:
    base = os.getenv("STAGING_API_BASE", "http://api:8000").rstrip("/")
    url = urlparse(base)
    if url.scheme != "http" or url.port != 8000 or not (
        url.hostname == "api" or (url.hostname and url.hostname.endswith(".railway.internal"))
    ):
        raise SystemExit("Health probe refuses any public endpoint")

    def get_json(path: str) -> dict:
        with urlopen(Request(base + path, method="GET"), timeout=8) as resp:
            if resp.status != 200:
                raise RuntimeError("Private staging health probe failed")
            payload = json.load(resp)
        if not isinstance(payload, dict):
            raise RuntimeError("Private staging response was not a JSON object")
        return payload

    ready = get_json("/ready")
    mode = get_json("/auth/capabilities")
    if ready.get("status") != "ready" or mode.get("login_mode") != "pilot":
        raise RuntimeError("Private staging is not ready for synthetic pilot access")
    print("PASS: private staging readiness and pilot-only authentication mode")


if __name__ == "__main__":
    run()
