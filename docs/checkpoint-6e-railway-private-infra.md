# Checkpoint 6E — Railway private cloud staging

## Scope

**Infrastructure preparation, not a public deployment.** The Railway project
`verity-private-staging` and its `staging` environment have four staged
services: `verity-postgres`, `verity-migrations`, `verity-api`,
`verity-worker`. No public domain or TCP proxy is authorized.

The cloud database must implement the **same least-privilege role model**
that already works in the local Docker Compose environment. The dedicated
`docker/postgres/Dockerfile.railway` builds an official PostgreSQL 16 image
with the existing initialization script. The script creates exactly two
restricted users: `lending_runtime` and `lending_lab_operator`.
The PostgreSQL owner `lending_migrator` is reserved for schema migrations
and privileged synthetic pilot-account provisioning.

The container init script runs **only on an empty PostgreSQL data volume**.
Do not reset or replace a persistent volume to replay role creation. Schema
migrations remain a distinct one-shot service.

## Railway service settings (staging only)

- `verity-postgres`: use the repository's
  `docker/postgres/Dockerfile.railway`, with a persistent volume mounted at
  `/var/lib/postgresql/data`. Set `PGDATA` to
  `/var/lib/postgresql/data/pgdata`. Set `POSTGRES_DB=lending_lab`,
  `POSTGRES_USER=lending_migrator` and three independently generated,
  high-entropy credentials for `POSTGRES_PASSWORD`,
  `APP_DB_PASSWORD`, and `LAB_DB_PASSWORD`.
- `verity-migrations`: build the root `Dockerfile`, run
  `python -m lending_lab.migrate` as a one-time task, with an owner-only
  `MIGRATION_DATABASE_URL`. Never share that URL with API or worker.
- `verity-api`: build the root `Dockerfile`, run
  `uvicorn lending_lab.api:app --host 0.0.0.0 --port 8000`, health-check
  `/health`, `APP_MODE=production`, `AI_MODE=deterministic`; provide only
  least-privilege runtime and lab database URLs plus signing keys.
- `verity-worker`: build root `Dockerfile`, run
  `python -m lending_lab.worker`, reuse only runtime/lab credentials and
  synthetic mode. Do not set a public domain.
- All services must use Railway **private DNS** internally. Do not add a
  public database TCP proxy. Keep Git sources pinned to the verified commit,
  not the default branch.

## Private release sequence (not yet executed)

1. Review the already-staged Railway changes, source pins and database image.
2. Configure credentials in Railway's sealed / protected secret storage,
   not in GitHub, chat, scripts or example env files. Never expose the
   migration-owner database credentials to the API or worker.
3. Confirm costs, persistence/backup policy, private networking, and project
   access permissions before approving any running services.
4. Provision PostgreSQL and its volume; verify initialization and distinct
   database roles from an authorized private job.
5. Run migrations once and check success before launching the API and worker.
6. Verify private API health and synthetic pilot login, role boundaries,
   logout, revocation, and audit trails from an internal test job.
7. Verify backups and restore (including migration rollback procedures), log
   redaction and retention, alerting, and service restart behavior.
8. Add strictly controlled TLS/browser ingress and per-IP throttling only
   after a separate security review. No real financial or identity data.

## Acceptance conditions

- [ ] Railway has the exact expected source code pinned.
- [ ] Official PostgreSQL image with role initialization passes CI.
- [ ] Private Railway staging service configuration is complete.
- [ ] PostgreSQL and the migration job are running and checked.
- [ ] API and worker have healthy deployments (without public domains).
- [ ] All cloud synthetic smoke tests and backup/restore tests pass.
- [ ] Independent security/privacy review before inviting people.

**Do not treat passing Docker CI as proof of a completed cloud deployment.**
