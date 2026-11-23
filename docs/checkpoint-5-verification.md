# Checkpoint 5 — Frontend integration verification

**Status:** implemented and verified locally on November 23, 2026. Not a real lending product.

## Application

A responsive React 19 + TypeScript + Vite frontend is located in `frontend/`. The interface is branded **Verity** and exposes separate borrower and operator workspaces after the existing signed demo login.

Borrower journey: list owner-scoped workflows; create a new synthetic application with the exact versioned lending-consent wording; evaluate deterministic rules; inspect the sorted illustrative offer comparison; create and explicitly consume a single-use approval; inspect status, simulator-ledger evidence and audit timeline; revoke lending consent directly; ask the server-rendered explanation assistant; view/grant/revoke separate optional AI-processing consent.

Operator journey: list supported fault scenarios; run bounded safe and intentionally unsafe trials with matching scenario, seed and trial count; retrieve run history, trial traces, effect counts, verified confirmations, duplicate counts, and invariant-violation denominators; optionally process a queued, already-approved synthetic dispatch through a **local-demo-only** operator route. This button never invents consent or an approval.

The frontend does not perform financial calculations or make decisions. It reads and renders backend evidence and keeps the bearer token in React component memory only (no localStorage, IndexedDB, URL tokens, or cookie persistence). A refresh requires reauthentication.

## Backend integration added

- `GET /workflows`: latest 50 workflows filtered by the authenticated borrower; no operator access.
- `GET /workflows/{workflow_id}/evidence/{view}`: explicit allowlist (`status`, `rules`, `offers`, `requirements`), invoking the original borrower-scoped evidence tools and schema validation.
- `GET /operator/lab/runs`: latest 30 isolated lab run summaries, operator only.
- `POST /operator/dispatch/process-next`: operator-only, local-demo-only single pass through the existing dispatch worker; no fresh authorization or idempotency keys.

The requested workflow's positive ownership/existence check now runs before optional Gemini classification. The regression test covers missing and cross-borrower workflow IDs with an injectable provider spy. The test was placed last in the shared-database AI module to avoid polluting subsequent tests with retained revoked AI grant metadata. The read-only requirements view now returns the current approval's identifier and workflow version so refresh does not lose the direct single-use approval action.

## Actual local verification

- macOS, Node 22.23.1, npm 10.9.8, PostgreSQL 16.16.
- TypeScript production build: **passed**, including a lazy-loaded Reliability Lab chart chunk.
- Fresh disposable PostgreSQL database `lending_lab_cp5_verify`, role-separated runtime / migrator / lab connections: **107 passed**, 0 failed, 0 skipped (102 previously reported C4 cases, 1 authorization regression, 4 new frontend API integration cases). JUnit XML: `/tmp/lending-lab-cp5-final.xml`.
- Browser automation against an actual FastAPI server and PostgreSQL database using Playwright Chromium: **4 passed**, 0 failed. Tests cover borrower creation/evaluation/AI, operator safe-vs-unsafe comparison, explicit approval + operator worker + confirmed synthetic submission, and mobile navigation.
- A separate clean `lending_lab_showcase` database was created and migrated for a usable demo. FastAPI served the compiled SPA and returned a successful database health check.
- `git diff --check` and Python compilation checked during implementation. Docker engine was unavailable, so a real Docker Compose startup is not claimed as observed.
- The optional Gemini adapter was not exercised with real credentials. Deterministic mode remains the fully tested default.

## Reproduction

```sh
# From the repository root, with a separately configured disposable PostgreSQL database:
REQUIRE_POSTGRES_TESTS=1 APP_MODE=local_demo \
  TEST_DATABASE_URL="$RUNTIME_TEST_DSN" \
  TEST_MIGRATION_DATABASE_URL="$MIGRATION_TEST_DSN" \
  TEST_LAB_DATABASE_URL="$LAB_TEST_DSN" \
  .venv/bin/pytest -q --junitxml=/tmp/verity-test.xml
.venv/bin/python scripts/assert_ci_test_results.py /tmp/verity-test.xml

cd frontend
npm ci
npm run build
npm run test:e2e    # requires a running local API at http://127.0.0.1:8000 and Playwright Chromium
```

Playwright tests write only synthetic records. Never point them at a production database. The browser scenarios use demo passwords and are not production authentication tests.

## Explicit limitations

- Only synthetic users, policies, offers, consent grants, ledger entries and application effects are used.
- The API and lab share local PostgreSQL infrastructure, with role-based separation. No real lender integration or external exactly-once guarantee.
- The intentionally unsafe baseline is a constructed experiment. Its violations are not production defects in the safe backend.
- No general AI agent or model-generated financial facts. Gemini classification remains optional and externally unverified.
- Docker Compose runtime, production deployment, and provider retention/compliance are not verified here.
- Demo environment and its documented credentials must not be exposed on a public network without independent production hardening.
