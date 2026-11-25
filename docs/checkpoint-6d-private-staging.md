# Checkpoint 6D — Private synthetic staging

## Status

**Code prepared for CI verification. Not yet deployed to a cloud host.**
This checkpoint stands up the existing production-mode API and worker with
PostgreSQL, migration owner, runtime and lab identities on an **internal-only
Docker network**, with **zero published host ports**. It is intentionally
inaccessible from a browser or the public Internet.

The smoke service is opt-in and creates temporary **synthetic** pilot accounts.
CI generates fresh, disposable secrets and destroys its database after tests.

## Reproduce locally

From a trusted machine with Docker Compose installed:

```bash
python scripts/create_staging_env.py
docker compose --env-file .env.staging.local -f docker-compose.staging.yml up -d --build db migrate api worker
STAGING_SMOKE_ENABLE=1 docker compose --env-file .env.staging.local -f docker-compose.staging.yml --profile smoke run --rm smoke
docker compose --env-file .env.staging.local -f docker-compose.staging.yml ps
```

These containers are **private to Docker**. A browser will not be able to
navigate to port 8000, by design.

For **disposable staging only**, remove all containers and stored data:

```bash
docker compose --env-file .env.staging.local -f docker-compose.staging.yml --profile smoke down -v
```

**Warning:** `down -v` destroys the entire local staging database.
The generated secret file is ignored by Git; protect and rotate it like a
credential. Never paste it in chat or commit it.

## Protections exercised by CI

- Compose configuration fails without explicitly provided staging secrets.
- All service networks are marked `internal: true`; zero host ports.
- Migration-owner credentials are **not** provided to API or worker.
- The API and worker use distinct, least-privilege runtime and lab DB roles.
- API and worker use read-only root filesystems.
- Production mode uses deterministic AI and rejects local-demo sign-in.
- Freshly provisioned synthetic user can authenticate, read an empty workspace
  and log out; a borrower cannot access the operator lab.
- CI destroys test credentials and PostgreSQL volumes after testing.

## Not included / release blockers

This is **not** an externally accessible hosted environment. A later step
must choose and configure a cloud service with a private ingress boundary,
TLS, external request throttling, observability, encrypted backups, and
documented recovery. Run a real browser journey and a third-party security
review before inviting pilot participants. No real names, financial records,
lender connections or actual loan transactions are authorized.

Do not merge this stack as proof of readiness for public borrowers.
