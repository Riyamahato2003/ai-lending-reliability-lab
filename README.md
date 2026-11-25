# AI Lending Reliability Lab

An original engineering demonstration of reliable workflow patterns using synthetic borrower data and three simulated lenders. It is not a clone of a lender product and makes no claim of real financial eligibility, preapproval, guaranteed offers, production use, regulatory certification, or compliance. All calculated offers are explicitly illustrative.

## Implemented application (Checkpoints 2–5)

Implemented here: FastAPI/Pydantic service, real PostgreSQL migrations and synthetic fixtures, distinct migration-owner/runtime/lab database roles, signed revocable demo sessions, borrower ownership checks, immutable security events, versioned deterministic rules, lender-specific outbound field allowlists, a persistent simulator ledger, ordered hash-chained audit, `submission_unknown` recovery, single-use HMAC-bound approvals, write-ahead dispatch, lease recovery, consent revocation and synthetic-data deletion, a deterministic operator-only Reliability Lab with safe/unsafe runs, and an evidence-grounded read-only explanation assistant. Gemini is optional and not required by CI. A responsive React + TypeScript browser application is implemented and served through FastAPI. Simulated ledger records do not establish exactly-once effects against an independent remote service. See [PLAN.md](PLAN.md) and the checkpoint verification reports.

## Public interactive sandbox (Checkpoint 6H)

**Open Verity directly:** [Public sandbox](https://verity-api-staging.up.railway.app)

The homepage opens the public workspace without any email, password or signup.
Visitors can create fictional applications, compare three illustrative lender offers,
approve a simulated request, deliberately lose its acknowledgement, reconcile the
original simulated effect, ask evidence-style explanation questions and run
safe-versus-unsafe reliability experiments.

**Public guest data lives in memory in each visitor's browser tab.**
The guest experience does not create shared borrower records or submit
authenticated backend API writes. Reloading the page or pressing Reset clears
all guest work. It makes no real lender submissions, credit decisions, or
external AI calls. This functional teaching simulator does **not** replicate
the durable PostgreSQL guarantees of the protected pilot.

The original database-backed borrower and operator workflows remain
invitation-only at `/?pilot=1`. They retain their existing authentication
and ownership protections; there is no public account registration.
Never submit actual personal or financial information. Live HTTP availability
does not imply regulated production readiness or completion of a security review.

## Explore the finished Verity demo (Checkpoint 5)

The browser app provides borrower application creation, purpose-specific lending consent, deterministic eligibility and lender comparison, explicit single-use approval, simulator-ledger status, audit timeline, read-only grounded assistant, optional independent AI-processing consent, and an operator-only Reliability Lab. All values and effects are synthetic.

**Local Mac / Homebrew PostgreSQL (already configured with migrator, runtime, and lab roles):**

```sh
./scripts/start_local_demo.sh
```

The script uses a dedicated `lending_lab_showcase` database, migrates it if needed, builds the frontend, and starts FastAPI on **http://127.0.0.1:8000**. It never points test fixtures at this showcase database. Stop any other server using port 8000 before starting it. For a portable alternative, use Docker Compose below (container runtime was unavailable on the development Mac and remains unverified).

Sign in with the **synthetic borrower** or **lab operator** accounts in the table below. For a complete local workflow, enter illustrative figures, evaluate, request an eligible offer, explicitly confirm the single-use approval, then sign in as operator to process the queued simulated dispatch. The UI never lets the assistant execute this protected sequence.

**Frontend development and browser tests:**

```sh
cd frontend
npm ci
npm run dev         # Vite on port 5173; proxies API requests to FastAPI on port 8000
npm run build       # TypeScript check and production bundle
npm run test:e2e    # Playwright Chromium against running FastAPI on port 8000
```

See [Checkpoint 5 verification](docs/checkpoint-5-verification.md) and [portfolio release boundaries](docs/release-readiness.md). The browser integration has real PostgreSQL-backed tests, but does not make this a production financial product.

## Run locally with Docker Compose

Requires Docker with Compose v2.

```sh
cp .env.example .env
docker compose up --build
```

The **web app** is at `http://localhost:8000`; OpenAPI docs are at `/docs`. Compose also starts a separate durable submission worker after migrations complete. Seeded synthetic accounts:

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

Compose initializes PostgreSQL with `lending_migrator`, `lending_runtime`, and `lending_lab_operator`. Migrations run in a one-shot service using the migration-owner DSN. Borrower and workflow operations use `lending_runtime`; operator lab runs use `lending_lab_operator` only after the user session is verified as an operator. PostgreSQL permissions deny the lab role access to borrower profiles, snapshots, consent grants, and workflow submissions.

## Reliability Lab API

The lab is an isolated backend experiment harness with a new interactive operator dashboard, not a real lender integration. Start Docker Compose, log in as the seeded `lab-operator`, then call:

1. `GET /operator/lab/scenarios` to list the five fixed fault schedules.
2. `POST /operator/lab/runs` once with `implementation: "safe"`, then again with `implementation: "unsafe"`, using identical scenario, seed, trial count, and revocation option.
3. `POST /operator/lab/compare` with the two returned run IDs.
4. `GET /operator/lab/runs/{run_id}` to retrieve per-trial traces and metrics.

This is an isolated teaching model in `lab_*` tables; it does not run the full production dispatch state machine and no real external lender or production credit system is involved. In a safe trial, the synthetic dispatch intent commits independently before the next PostgreSQL transaction simulates the lender effect and recovery logic. That simulation does not reproduce an external network boundary. Separate real worker process crash/restart tests are in Checkpoint 3A. If a lab process stops mid-run, its immutable partial run can remain `running`; it is not a completed measurement and is not automatically resumed.

Example safe request body (send with the operator bearer token):

```json
{"implementation":"safe","scenario":"EFFECT_COMMITTED_RESPONSE_LOST","seed":42,"trials":10}
```

Runs are limited to 100 trials, request bodies to 16 KiB, and one active synchronous experiment per API process. `revoke_before_recovery` is supported only for request-lost and effect-committed scenarios. Run records and ledgers live only in restricted `lab_*` tables and contain synthetic scenario data, not borrower data. The unsafe variant deliberately omits named safety controls and is a teaching baseline; its results are not measured defects in production lending or the safe application. The observed safe/unsafe sample is finite evidence for that seed and schedule, not a universal reliability guarantee. Repeated seeds reproduce logical outcomes, not wall-clock durations or nondeterministic concurrency interleavings.

## Evidence-grounded assistant (Checkpoint 4)

Assistant APIs power the integrated read-only borrower interface. `GET /workflows/{id}/assistant/capabilities` shows the selected mode and four read-only evidence topics; `POST /workflows/{id}/assistant/explain` accepts a bounded question. The server checks workflow ownership, reads only that borrower's deterministic records, and composes the answer and references itself. It never grants consent, approves, or submits.

`AI_MODE` is `deterministic` by default; `mock` uses a reproducible local classifier; `gemini` is optional. Install with `pip install -e '.[gemini]'` and set `GEMINI_API_KEY` plus `GEMINI_MODEL` through environment configuration. Gemini receives the bounded question and fixed system instructions, not workflow records or financial snapshots. Because a question may contain sensitive text, the borrower must first grant separate versioned consent using `GET /assistant/consent` and `POST /assistant/consent`; revoke directly with `POST /assistant/consent/{grant_id}/revoke`. Revocation waits for an already-started bounded provider request, then blocks future requests. It does not revoke lender-comparison consent or interrupt lending reconciliation. CI makes no live Gemini requests. Assistant answers remain illustrative and are not credit decisions.

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

Without all three test URLs, PostgreSQL integration tests skip unless `REQUIRE_POSTGRES_TESTS=1`, which fails setup if any DSN is missing. Hypothesis checks amortization accounting, paise rounding, final payment residuals, and stable comparison behavior. The GitHub Actions workflow provisions PostgreSQL 16, bootstraps separate roles, runs tests with JUnit output, and asserts required Checkpoint 2, 3A, 3B, and 3C database cases are present with no skips. A configured workflow is not evidence of an observed run; current run evidence is in the 3C report.

## Decisions and boundaries

- See [PLAN.md](PLAN.md) for the full architecture, threat model, data model, acceptance criteria, and checkpoint boundaries.
- PostgreSQL `SKIP LOCKED` leases are used for durable work claiming. Redis/Celery are omitted because no concrete need has been shown.
- A simulator-side persistent ledger, separate from workflow records, is authoritative for the simulated side effect. Same key/same payload replays the same response; same key/different payload conflicts. Since the simulator currently shares the PostgreSQL server, this is not evidence of exactly-once effects against an independent service.
- A timed-out external submission must become `submission_unknown`, then reconcile with the lender ledger using the same idempotency key before retry. A timeout is not a rejection. Submission steps are not exposed as borrower-authorized API transitions in this checkpoint; the generic borrower transition endpoint only permits cancellation.
- Audit records have ordered per-workflow sequences, database update/delete rejection, and a SHA-256 chain. This is tamper-evident against ordinary edits, not tamper-proof against a database owner who can rewrite data and trigger definitions.
- Offers are illustrative monthly amortization in integer INR paise; monthly interest is calculated from annual basis points, rounded half-up each period, and the final payment absorbs residual paise. Comparison label: “sorted by estimated total repayment, then lender ID.” This is not a real financial offer or a “best loan” claim.
- Purpose-specific consent text is versioned and immutable; grants retain the exact text HMAC, purpose, key version, and text-version reference. Borrowers can revoke directly through the API. Revocation stops unsent work, invalidates approvals, and deletes synthetic financial inputs and associated sensitive workflow payloads. Existing committed intents remain available as minimal reconciliation metadata, and the simulator ledger remains authoritative. Database deletion does not remove copies from retained backups, WAL archives, or exports; this project makes no cryptographic-erasure claim. See the Checkpoint 3B verification report for tested boundaries.
- Security events are stored separately from loan workflow audit events. They record fixed event/reason values and synthetic actor IDs, never credentials or tokens.
- Demo bearer tokens are held only in React component memory, not localStorage, URL parameters, or application logs. Authorization headers are not browser ambient credentials; a future cookie-based design would need SameSite and CSRF defenses.
- Gemini remains optional. When installed and configured, the maintained `google-genai` SDK is used; credentials and model selection come from environment configuration. CI uses deterministic/mock behavior and does not make live requests. The model selects only a typed read-only topic; all facts and user-visible prose come from server-side evidence and templates.

## Verification record

Checkpoint 2–4 historical evidence is in the checkpoint reports. The final local integrated suite on disposable PostgreSQL 16.16 recorded **107 passed, 0 failed, 0 skipped**. The browser UI completed **4/4 Playwright Chromium journeys**, including borrower evaluation, read-only assistant, safe-vs-unsafe operator comparison, explicit approval + simulated worker confirmation, and mobile navigation; see [Checkpoint 5 verification](docs/checkpoint-5-verification.md).

Docker Compose and live Gemini remain unverified on this development Mac. A successful test sample is finite evidence, not universal reliability or an external lender guarantee. Final hosted CI results must be checked against the exact committed SHA.
