# AI Lending Reliability Lab

An original engineering demonstration of reliable workflow patterns using synthetic borrower data and three simulated lenders. It is not a clone of a lender product and makes no claim of real financial eligibility, preapproval, guaranteed offers, production use, regulatory certification, or compliance. All calculated offers are explicitly illustrative.

## Implemented backend scope (Checkpoints 2 and 3A)

Implemented here: FastAPI/Pydantic service, real PostgreSQL migrations and synthetic fixtures, distinct migration-owner/runtime/lab database roles, signed revocable demo sessions, borrower ownership checks, separate immutable security events, versioned deterministic rules, immutable evaluation snapshots, lender-specific outbound field allowlists, persistent simulator-side idempotency ledger, ordered hash-chained audit rows, `submission_unknown` recovery, single-use HMAC-bound approvals, write-ahead dispatch intents, a separate PostgreSQL worker, lease recovery, same-key simulator reconciliation, immutable audit events, and restricted operator conflict review. Consent revocation, fault-injection lab, AI, and frontend remain future checkpoints. The worker and API share the restricted application DB credential; the simulator ledger models but cannot prove exactly-once effects against an independent remote service. See [PLAN.md](PLAN.md), [Checkpoint 2 verification](docs/checkpoint-2-verification.md), and [Checkpoint 3A verification](docs/checkpoint-3a-verification.md).

## Run locally with Docker Compose

Requires Docker with Compose v2.

```sh
cp .env.example .env
docker compose up --build
```

The API is at `http://localhost:8000`; OpenAPI docs are at `/docs`. Compose also starts a separate durable submission worker after migrations complete. Seeded synthetic accounts:

| Username | Default demo password | Role |
| --- | --- | --- |
| `borrower-001` | `borrower-demo` | Borrower bound to borrower 001 |
| `borrower-002` | `borrower-demo` | Borrower bound to borrower 002 |
| `lab-operator` | `operator-demo` | Operator only |

These are public demo credentials, not suitable for deployment. The sample `.env.example` explicitly sets `APP_MODE=local_demo`; startup rejects sample secrets/passwords in the default production mode and local mode accepts only loopback or Compose database hosts. Change the signing secret, migration/runtime/lab database passwords, and demo passwords through environment configuration when running locally; URL-encode special characters in DSN credentials. Demo passwords use PBKDF2-HMAC-SHA-256 (600,000 iterations, random 16-byte salt). The server issues expiring HMAC-signed bearer sessions stored for logout revocation and checks role/borrower binding against server-side user and session rows; callers cannot choose their role or borrower binding. `POST /demo/sessions/logout` revokes the current token. This is a local demonstration identity mechanism and must never be represented as production authentication.

Example journey:

```sh
TOKEN=$(curl -s localhost:8000/demo/sessions -H 'content-type: application/json' \
  -d '{"username":"borrower-001","password":"borrower-demo"}' | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -s localhost:8000/workflows -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"purpose":"illustrative_offer_comparison","consent_text":"I consent to synthetic illustrative lender comparison for this demo."}'
```

Stop the stack with `docker compose down`. To remove the local PostgreSQL volume and recreate synthetic fixtures from scratch, run `docker compose down -v` and start again.

Compose initializes PostgreSQL with `lending_migrator`, `lending_runtime`, and `lending_lab_operator`. Migrations run in a one-shot service using the migration-owner DSN; the API connects only as `lending_runtime`. The lab database role is limited to lab tables and is reserved for later lab administration.

## Tests

Tests that exercise transactions and database enforcement require a **disposable real PostgreSQL database**. Do not point test URLs at data you need: the suite truncates application tables. For Compose:

```sh
docker compose up -d db
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
TEST_DATABASE_URL=postgresql://lending_runtime:lending-runtime-demo@localhost:5432/lending_lab \
TEST_MIGRATION_DATABASE_URL=postgresql://lending_migrator:lending-migrator-demo@localhost:5432/lending_lab \
TEST_LAB_DATABASE_URL=postgresql://lending_lab_operator:lending-lab-demo@localhost:5432/lending_lab \
pytest
```

Without all three test URLs, PostgreSQL integration tests skip unless `REQUIRE_POSTGRES_TESTS=1`, which fails setup if any DSN is missing. Hypothesis checks amortization accounting, paise rounding, final payment residuals, and stable comparison behavior. The GitHub Actions workflow provisions PostgreSQL 16, bootstraps separate roles, runs tests with JUnit output, and executes an assertion that required integration and Checkpoint 3A tests are present and no tests skipped. A configured workflow is not evidence of an observed CI run.

## Decisions and boundaries

- See [PLAN.md](PLAN.md) for the full architecture, threat model, data model, acceptance criteria, and checkpoint boundaries.
- PostgreSQL `SKIP LOCKED` leases are used for durable work claiming. Redis/Celery are omitted because no concrete need has been shown.
- A simulator-side persistent ledger, separate from workflow records, is authoritative for the simulated side effect. Same key/same payload replays the same response; same key/different payload conflicts. Since the simulator currently shares the PostgreSQL server, this is not evidence of exactly-once effects against an independent service.
- A timed-out external submission must become `submission_unknown`, then reconcile with the lender ledger using the same idempotency key before retry. A timeout is not a rejection. Submission steps are not exposed as borrower-authorized API transitions in this checkpoint; the generic borrower transition endpoint only permits cancellation.
- Audit records have ordered per-workflow sequences, database update/delete rejection, and a SHA-256 chain. This is tamper-evident against ordinary edits, not tamper-proof against a database owner who can rewrite data and trigger definitions.
- Offers are illustrative monthly amortization in integer INR paise; monthly interest is calculated from annual basis points, rounded half-up each period, and the final payment absorbs residual paise. Comparison label: “sorted by estimated total repayment, then lender ID.” This is not a real financial offer or a “best loan” claim.
- Purpose-specific consent text is versioned and immutable; grants retain the exact text HMAC, purpose, key version, and text-version reference. Borrowers can revoke directly through the API. Revocation stops unsent work, invalidates approvals, and deletes synthetic financial inputs and associated sensitive workflow payloads. Existing committed intents remain available as minimal reconciliation metadata, and the simulator ledger remains authoritative. Database deletion does not remove copies from retained backups, WAL archives, or exports; this project makes no cryptographic-erasure claim. See the Checkpoint 3B verification report for tested boundaries.
- Security events are stored separately from loan workflow audit events. They record fixed event/reason values and synthetic actor IDs, never credentials or tokens.
- Demo bearer tokens are stored in memory by any future client, not URLs/logs. Authorization headers are not browser ambient credentials; a future cookie flow must add SameSite and CSRF token protections.
- Gemini remains optional and absent in this checkpoint; CI will use a deterministic mock in the later assistant checkpoint.

## Verification record

Checkpoint 3B implementation and test evidence: [docs/checkpoint-3b-verification.md](docs/checkpoint-3b-verification.md).

Checkpoint 2 evidence (historical; current Checkpoint 3A evidence is in `docs/checkpoint-3a-verification.md`). Executed in this workspace on 2026-11-22:

- `APP_MODE=local_demo REQUIRE_POSTGRES_TESTS=1 TEST_DATABASE_URL=postgresql://lending_runtime:runtime-test-password@127.0.0.1:55432/lending_lab_final_verify TEST_MIGRATION_DATABASE_URL=postgresql://lending_migrator@127.0.0.1:55432/lending_lab_final_verify TEST_LAB_DATABASE_URL=postgresql://lending_lab_operator:lab-test-password@127.0.0.1:55432/lending_lab_final_verify .venv/bin/pytest -q --junitxml=/tmp/ai-lending-pytest.xml` — **34 passed, 0 failed, 0 skipped**, using fresh local PostgreSQL 16.16 and Python 3.13.15.
- `.venv/bin/python scripts/assert_ci_test_results.py /tmp/ai-lending-pytest.xml` — passed: 34 tests, no failures/errors/skips, required integration test names present.
- `.venv/bin/pytest -q` without PostgreSQL DSNs — **12 passed, 22 skipped**. CI mode separately fails if required DSNs are absent.
- `.venv/bin/python -m compileall -q lending_lab tests scripts` — passed.
- YAML parse of `.github/workflows/ci.yml` and `docker-compose.yml` — passed.
- `git diff --check` — passed.

Pytest emitted one upstream Starlette deprecation warning about using `httpx` with its test client; it did not affect the results. Docker is unavailable in this environment, so `docker compose up --build` is unverified. The CI workflow was not triggered or observed; there is no configured Git remote and therefore no CI run link. Zero observed test failures are not proof of 100% reliability. The lab measurement harness is not implemented in this checkpoint.
