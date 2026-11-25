# Checkpoint 6C — separately provisioned pilot accounts

## Status

Implemented on a **development branch** for review and automated regression testing.
**Not a production authorization or public deployment sign-off.** No real lending
integration or real customer data is permitted.

## Design

- `APP_MODE=local_demo`: existing synthetic seeded demo users and
  `/demo/sessions`. The frontend has no embedded default passwords.
- `APP_MODE=production`: separately provisioned `pilot_accounts`;
  `POST /pilot/sessions` accepts credentials created by a privileged operator.
  No browser-based signup, and seeded demo accounts are rejected.
- `GET /auth/capabilities` tells the frontend which sign-in path to show.
  The response never contains credentials or privileged configuration.
- Passwords are salted PBKDF2-SHA256 hashes using the existing password
  verifier. A one-time random password is generated at provisioning or reset.
  No plaintext password is stored in the database or the repository.
- Every pilot account has a unique server-assigned identifier and (for the
  borrower role) a new synthetic `borrower_profiles` row. Operator accounts
  receive no borrower profile.
- Signed bearer sessions include an explicit pilot audience and have their
  own `pilot_sessions` database table. Validation checks the signature,
  audience, expiry, active session, active account, current role, and borrower
  binding. Revocation, account disablement, and credential reset invalidate
  existing sessions.
- A shared PostgreSQL counter limits a username to eight attempts per
  15-minute window, across API instances. This is **not a substitute** for
  ingress/IP-based anti-abuse protection and security monitoring.

## Provision or disable a pilot account

First prepare **an isolated staging PostgreSQL database**, apply all
migrations, and configure non-demo secrets. The commands below must run
in a trusted administration environment, using migration-owner credentials
available to the CLI but **not** to the publicly reachable API service.

Do not paste passwords, database URLs or generated secrets into GitHub,
issue comments or ChatGPT. Keep the output of these commands private.

```bash
# After setting APP_MODE=production and the correct secure DSNs/secrets:
python -m lending_lab.migrate
python -m lending_lab.pilot_admin create --username tester@example.com --role borrower
python -m lending_lab.pilot_admin create --username operator@example.com --role operator
python -m lending_lab.pilot_admin reset-password --username tester@example.com
python -m lending_lab.pilot_admin disable --username tester@example.com
```

The `create` and `reset-password` commands print a generated password
exactly once. Deliver it using an approved private channel, ask recipients
to keep it in a password manager, and treat the current design as a
restricted technical pilot rather than a general public account system.

## Required deployment boundaries

1. **Synthetic data only.** Do not invite real borrowers, collect financial
   records, or connect real lenders yet.
2. Configure separate least-privilege application and lab database
   connections. The migration-owner connection must not be available to the
   running API process after migrations.
3. Place the API behind TLS, private staging access, and an ingress control
   that enforces IP-level rate limits and reasonable request limits.
4. Review authorization and token handling, origin policy, session lifecycle,
   logs, secret rotation, backups, data retention and account recovery.
   Bearer tokens currently remain in in-memory React state and disappear on
   refresh; this intentionally avoids persistent browser token storage.
5. Complete the login flows in a real browser, test multiple users from
   different devices, and independently review the auth implementation.
6. Configure database migrations and role creation before app and worker
   startup. Do not use the local Compose stack for public hosting.
7. No public staging domain is approved until these checks are completed.

## Remaining feature gaps

- Email verification / invitation acceptance is not implemented; operators
  manually issue accounts and transmit one-time credentials.
- No MFA, self-service password reset, per-IP limiter or automated abuse
  detection. Those must be reviewed before widening access.
- No separate organization/tenant hierarchy. Every borrower account is
  individually bound to its own synthetic profile.
- No live loan submissions or financial decisions.
- Browser Playwright regression flow should be re-run using explicit local
  demo credentials (they are no longer pre-filled).
- Production environment still requires non-demo session, approval, privacy,
  and legacy demo-password configuration to pass startup validation, even
  though legacy demo accounts are disabled.

## Acceptance criteria

- [ ] New pilot authentication integration tests pass on disposable PostgreSQL.
- [ ] Existing backend tests and frontend build remain green.
- [ ] Local synthetic Compose smoke test passes.
- [ ] Individual pilot sign-in, role checks, logout, disablement, password
      reset and throttling behave as tested.
- [ ] Manual staging browser test completed.
- [ ] Independent security/privacy review completed before real-user access.

No public deployment is part of this checkpoint.
