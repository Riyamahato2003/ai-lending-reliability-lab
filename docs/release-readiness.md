# Portfolio release readiness — Verity / AI Lending Reliability Lab

## What is ready

The repository now contains the deterministic lending workflow, consent lifecycle, durable approvals/dispatch, audit and simulator reconciliation, the isolated operator Reliability Lab, the evidence-grounded explanation assistant, and a responsive integrated browser UI.

The app can be demonstrated locally with synthetic borrower and operator accounts. It provides an end-to-end visible path from explicit lending consent to deterministic evaluation, current illustrative offers, single-use approval consumption, a simulated effect, and authoritative ledger-backed status.

Local acceptance evidence:
- **107 PostgreSQL / Python tests passed** on a disposable role-separated database.
- **4 Chromium end-to-end journeys passed** using Playwright and a running API.
- React/TypeScript production bundle built successfully.
- Backend root path served the compiled SPA; health endpoint connected to PostgreSQL.

## Deployment boundaries

This is a **portfolio engineering demo**, not a real financial service. Do not publish publicly with demo credentials and sample secrets, even though it has database role segregation and authorization tests. Before internet exposure, configure non-demo secrets, real identity management, production security review, secure cookies/session management, CSRF/CORS decisions, TLS, telemetry/retention, backup lifecycle, process isolation, and operational monitoring.

The Dockerfile builds the React frontend in a Node stage and serves the resulting assets through the FastAPI image. The Compose configuration retains separate migrator/runtime/lab PostgreSQL identities and the separate worker. Docker was not installed on the development Mac, so Compose runtime is **unverified**.

Gemini is optional and no live API call has been verified. The model selects only an explanation topic; deterministic server code produces facts. It cannot authorize a lender effect. Separate AI-processing consent governs any outbound question transmission.

Do not describe fault experiments as proof of universal reliability or exactly-once delivery to a remote lender.

## Final release checklist

- [x] Backend ownership, read-only presentation endpoints and operator privileges exercised.
- [x] Existing 3A/3B/3C/4 regression suite preserved and extended.
- [x] Borrower and operator browser journeys exercised against real PostgreSQL.
- [x] Frontend production bundle built.
- [x] Demo run uses a clean, dedicated local database.
- [ ] Docker Compose build/start independently witnessed.
- [ ] Live Gemini provider request validated with explicit consent and test credentials.
- [ ] Hosted CI on final source commit observed successful.
- [ ] Production/Internet deployment (intentionally out of scope without non-demo security hardening).
