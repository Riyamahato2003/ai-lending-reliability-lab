# Checkpoint 6F — Synthetic pilot security and release review

**Status:** engineering self-review, not an independent penetration test,
legal opinion, RBI determination, or security certification.

## Architecture and trust boundaries

- A private Railway FastAPI service serves the compiled React UI and REST API.
- The API and separate worker access PostgreSQL through restricted runtime/lab
  identities. The schema-migration service alone holds the migration-owner URL.
- Individual pilot identities are manually provisioned via a restricted CLI.
  Bearer tokens are signed, short-lived and revoked server-side. React keeps
  tokens in memory, not localStorage or cookies.
- The reliability laboratory and lenders are **synthetic simulations**.
  They neither authorize nor submit real credit applications.

## Threat review

| Threat | Current mitigation | Residual risk / gate |
| --- | --- | --- |
| Seeded demo credentials online | Production rejects demo sign-in and demo tokens | Retest at ingress after domain configuration |
| Account takeover / guessing | Salted PBKDF2 hashes; 8 attempts per username per 15 min | Add trusted edge/IP throttling, alerting, MFA/invite acceptance |
| Tenant / borrower data exposure | Session role/borrower binding checked against DB on requests | Independent authorization tests and security assessment |
| Replay of approved effects | Single-use approvals, idempotent ledger, durable worker and audit | Cloud crash/recovery game day and negative integration tests |
| Stale/unapplied schema | API and worker now wait for required schema before starting | Verify deployment ordering on Railway after promoting code |
| Oversized request attack | 16 KB cap across all state-changing JSON endpoints | Edge bandwidth request limits and defensive monitoring |
| Browser data disclosure | Security response headers, no-store auth/workflow responses, in-memory bearer | Content Security Policy after browser compatibility tests, TLS |
| Secrets in Git | Runtime secrets stored in Railway environment variables | Access review, rotation plan, prohibit secrets in logs |
| Database loss | Persistent staging volume; pg_dump/pg_restore verified in disposable CI | **Railway live backup schedule and restore drill remain unverified** |
| Real borrower / lender effects | Deterministic synthetic simulator; no real integrations | Contractual permissions, regulatory/privacy review before production |
| Operator abuse | Separate operator role and restricted APIs | Independent operator provisioning/review, experiment quotas |
| Logs and retention | Correlation IDs, bounded security-event details | Redaction and retention validation, alert routing |

## Evidence from automated checks

- Backend PostgreSQL integration tests.
- Local-only Docker startup smoke.
- Internal Docker production-mode synthetic authentication smoke.
- Production-mode database schema readiness checks.
- Disposable PostgreSQL dump/restore verification.
- Chromium UI regression suite for local synthetic borrower/operator journeys
  (CI results must be checked; don't imply passing before observed).
- Railway one-shot internal smoke (PASS): pilot sign-in, logout, role
  boundaries and demo denial without a public domain.

## Release gates

- [ ] Confirm all latest CI suites pass, including browser journeys.
- [ ] Deploy revised startup guards and test /ready on private Railway.
- [ ] Configure scheduled live Railway backups and validate restore.
- [ ] Configure an alert destination for crashed deployments, storage
      exhaustion and abnormal authentication.
- [ ] Add ingress IP throttling and protected TLS entrypoint before exposing URL.
- [ ] Independently validate access controls, browser input handling and privacy.
- [ ] Invite individually provisioned testers using secure credential delivery.
- [ ] No PAN, Aadhaar, bank statements, real customers or actual loan offers.
- [ ] Obtain explicit separate authorization for any real lender integration.

**Do not claim production financial-service readiness from this review.**
