"""Validate normalized Compose JSON without printing secret environment values."""
import json
import sys

config = json.load(sys.stdin)
services = config["services"]
expected = {"db", "migrate", "api", "worker", "smoke"}
assert set(services) == expected, "Unexpected private staging services"
networks = config["networks"]
assert networks and all(n.get("internal") is True for n in networks.values())
for name, service in services.items():
    assert not service.get("ports"), f"{name} publishes a host port"
    assert not service.get("privileged"), f"{name} has elevated privileges"
    assert service.get("network_mode") not in ("host", "bridge"), name
    attached = service.get("networks", {})
    names = set(attached if isinstance(attached, list) else attached.keys())
    assert names and names.issubset(networks.keys()), name
for name in ("api", "worker"):
    svc = services[name]
    env = svc["environment"]
    assert env["APP_MODE"] == "production"
    assert env["AI_MODE"] == "deterministic"
    assert "MIGRATION_DATABASE_URL" not in env
    assert env["DATABASE_URL"].startswith("postgresql://lending_runtime:")
    assert env["LAB_DATABASE_URL"].startswith("postgresql://lending_lab_operator:")
    assert svc.get("read_only") is True
for name in ("migrate", "smoke"):
    env = services[name]["environment"]
    assert env["MIGRATION_DATABASE_URL"].startswith("postgresql://lending_migrator:")
assert "smoke" in services["smoke"].get("profiles", [])
print("PASS: internal-only staging, no host ports, no privileged API credentials")
