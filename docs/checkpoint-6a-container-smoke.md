# Verity Checkpoint 6A — container smoke verification

## Status

Prepared for review. **Not deployed**, **not production-ready**, and **not safe to expose to the internet**.

This initial change makes the existing container more conservative and adds an automated smoke test for the *synthetic local demo*. It does not introduce production authentication, third-party financial integrations, backups, rate limiting, or a public deployment.

## Changes

- The Docker image runs the FastAPI service as an unprivileged user.
- Docker reports API and database health through the existing `/health` endpoint.
- The build context excludes environment files, private keys, local data, logs, and developer artifacts.
- GitHub Actions builds the Compose stack and checks that PostgreSQL, FastAPI, and the compiled React interface start together.

## Local verification

From the repository root, on a machine with Docker installed:

```bash
docker compose build
docker compose up -d
curl -fsS http://127.0.0.1:8000/health
docker compose ps
docker compose down
```

The Compose file still sets `APP_MODE=local_demo`, uses sample passwords when none are provided, and publishes ports. This command sequence is **only for a private local development machine**. Do not run this stack on a publicly reachable host.

The new GitHub Actions smoke test uses an isolated runner and synthetic data, and cleans up its containers and volumes after completion.

## Blockers before any public or invited-user deployment

1. Replace fixed `demo_users` identities and `/demo/sessions` with separately provisioned pilot users and appropriately secured sessions.
2. Remove demo credential presets and the account-switcher from the public frontend.
3. Restrict the Reliability Lab and operator actions to independently authorized operators.
4. Use private database networking, least-privilege credentials, proper secret management, encrypted backups, and deletion/retention policies.
5. Add rate limiting, secure session protections, request origin/CSRF policy where relevant, scoped logging and monitoring, and a dedicated security review.
6. Deploy and verify separate API and worker services, explicit migrations, TLS, monitoring, and recovery procedures.
7. Use synthetic information only until approved agreements and applicable privacy and financial-services requirements are met.

## Acceptance evidence needed

- [ ] Docker image builds on a runner with Docker.
- [ ] Compose migration, API, worker, and PostgreSQL startup pass.
- [ ] API `/health` reports connected and HTML frontend is served.
- [ ] Existing 107 backend tests and 4 browser tests remain green.
- [ ] No public endpoints have been deployed.

**Checkpoint 6A is complete only after its automated verification passes.**
