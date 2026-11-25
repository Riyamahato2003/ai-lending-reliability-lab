# Checkpoint 6B — production must reject demo accounts

## Scope and rationale

Verity currently exposes *synthetic demonstration accounts* through
`POST /demo/sessions`, and the migration script seeds fixed local usernames.
These accounts are suitable for isolated developer testing, **not a public
pilot**.

This checkpoint enforces the boundary in code:

- `/demo/sessions` is unavailable unless `APP_MODE=local_demo`.
- Previously issued demo tokens are rejected when the app runs with
  `APP_MODE=production`.
- Migration runs still install schema changes, but do not create or rotate
  demo accounts in production.
- Dedicated unit regressions verify these refusals happen **before**
  connecting to the database.
- Existing local-only demonstration flows remain supported.
- The development Compose stack publishes API and PostgreSQL ports on `127.0.0.1` only, and a CI check rejects Internet-facing Compose port bindings.

## What this does NOT deliver

This patch intentionally makes the current application **unusable for sign-in
when APP_MODE=production**. That is a *safe failure* until genuine pilot
authentication exists. Do **not** deploy it and claim that real users can log in.

The React login screen still contains local demonstration account presets.
No public website should serve that screen in a pilot release.

## Next implementation: invite-only pilot access

1. Define and review the pilot identity model: unique individual accounts,
   verified invitations, explicitly assigned roles and borrower bindings.
2. Build properly rate-limited production authentication and revocable
   sessions, with independent provision/revoke workflows and no fixed seeds.
3. Switch the browser login interface based on server-provided environment
   capabilities, never shipping demo-password presets into public builds.
4. Add negative and positive integration tests for account separation,
   credential failure, disabled/revoked users, expired sessions, and operator
   authorization.
5. Configure TLS, origin policy, appropriate secure session storage, secrets,
   backups, event monitoring and user-data retention.
6. Deploy to a *private staging URL*, verify with synthetic data, and conduct
   an independent security review before allowing pilot testers.

For the initial pilot, collect **no real bank statements, PAN/Aadhaar,
customer records, or actual lending applications**. The synthetic lender
simulation must remain clearly labeled.

## Review gate

- [ ] New backend regression tests pass.
- [ ] Existing backend CI passes.
- [ ] Local Compose smoke workflow passes.
- [ ] No production demo login is possible.
- [ ] No application is publicly deployed as part of this change.
