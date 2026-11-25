"""Database and HTTP protection regression tests."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from lending_lab.api import app
from lending_lab import readiness


def test_schema_readiness_as_runtime_role(db, monkeypatch):
    monkeypatch.setattr(readiness, "get_settings", lambda: SimpleNamespace(app_mode="production"))
    readiness.check_schema_ready()


def test_ready_endpoint_and_security_headers(db):
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_auth_responses_disable_cache(db):
    response = TestClient(app).get("/auth/capabilities")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_ready_returns_503_when_schema_missing(monkeypatch):
    def broken():
        raise RuntimeError("schema missing")
    monkeypatch.setattr("lending_lab.api.check_schema_ready", broken)
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "Application schema not ready"


def test_worker_readiness_times_out_without_logging_secrets(monkeypatch):
    monkeypatch.setattr(readiness, "check_schema_ready",
                        lambda: (_ for _ in ()).throw(RuntimeError("secret database URL")))
    moments = iter([100.0, 101.0])
    monkeypatch.setattr(readiness.time, "monotonic", lambda: next(moments))
    with pytest.raises(RuntimeError, match="database schema readiness timeout"):
        readiness.wait_for_schema(timeout_seconds=0.1, interval_seconds=0.05)


def test_worker_readiness_returns_after_transient_failure(monkeypatch):
    attempts = iter([False, True])
    calls = []
    def probe():
        calls.append("attempt")
        if not next(attempts):
            raise RuntimeError("schema initializing")
    monkeypatch.setattr(readiness, "check_schema_ready", probe)
    monkeypatch.setattr(readiness.time, "sleep", lambda _: None)
    readiness.wait_for_schema(timeout_seconds=2.0, interval_seconds=0.1)
    assert calls == ["attempt", "attempt"]
