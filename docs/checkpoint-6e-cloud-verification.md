# Checkpoint 6E — Railway cloud verification record

**Verified:** 2026-11-26 (India time)  
**Scope:** private synthetic-data staging only; no external users.

## Railway infrastructure

Project: `verity-private-staging`  
Environment: `staging`

| Service | Deployment | Runtime evidence |
| --- | --- | --- |
| `verity-postgres` | SUCCESS | 1 running replica, PostgreSQL ready for connections |
| `verity-migrations` | SUCCESS | One-shot migration process finished, 0 running replicas |
| `verity-api` | SUCCESS | 1 running replica; Railway health check `/health` passes after setting `PORT=8000` |
| `verity-worker` | SUCCESS | 1 running replica, 0 crashed |

A persistent **500 MB** PostgreSQL volume is mounted at
`/var/lib/postgresql/data`. The database starts from the repository's
`docker/postgres/Dockerfile.railway`, which packages the least-privilege role
bootstrap script.

All service sources were pinned to verified GitHub commits:
application/worker/migrations use the Checkpoint 6D commit; database uses
the Checkpoint 6E commit. The source is **not** the unverified main branch.

## Exposure verification

For **each** of the four services, Railway reported:

- Zero Railway-provided public domains.
- Zero custom domains.
- Zero public TCP proxies.

The Railway project is private. The API is accessible only through internal
service networking and cannot be opened in a browser from the Internet.

## Startup observations and fixes

On the first rollout, the API health check probed Railway's default port
8080 while Uvicorn listened on 8000. The API's `PORT=8000` environment
setting fixed the health check; the replacement deployment became SUCCESS.

The worker started before private database DNS/schema initialization and
logged an initial connection/schema error. It recovered after PostgreSQL
initialization and migrations. This demonstrates the importance of verifying
startup ordering; before an external pilot, add a bounded, explicit
database/schema readiness check rather than relying on retry/restarts.

## CI evidence

At the Checkpoint 6E GitHub commit, all three workflow suites passed:

- Backend: **121 tests passed, 0 failed, 0 skipped**.
- Existing local Compose smoke: success.
- Isolated private staging Compose: success, including production-mode pilot
  login, role boundary, logout, and rejection of seeded demo accounts.

These CI tests use disposable synthetic data and do **not** replace
cloud-specific browser testing or an independent security review.

## Outstanding release gates (not completed)

- [ ] Perform an authenticated internal cloud pilot-user smoke test.
- [ ] Verify database backup, restore, and point-in-time recovery strategy.
- [ ] Add alerts for service failures, connection errors, disk usage, and
      abnormal authentication attempts.
- [ ] Test crash/restart behavior and durable worker queue recovery in cloud.
- [ ] Add network-level/IP request throttling and secure external TLS ingress.
- [ ] Independently review account issuance, session management, security
      headers, audit logs, data deletion, and privacy requirements.
- [ ] Test real browser experiences from independent pilot accounts.
- [ ] Obtain explicit approval before exposing a public domain or accepting
      real financial or personal data.

**No production loan processing, customer onboarding, or lender integration
is authorized by this checkpoint.**
