# Checkpoint 6G — Public synthetic pilot release gate

Status: **hardening proposed; public exposure NOT yet authorized**.

## Verified baseline

- Private Railway staging uses the `checkpoint-6f-cloud-readiness` branch.
- The API, worker, PostgreSQL, migrations and private health probe had healthy
  deployment status at the last inspection.
- The backend CI, container smoke and private-staging GitHub Actions workflows
  passed at commit `ceeda36e084a43e8259f0fcdfa51d66f94825744`.
- No public Railway domain was attached at that checkpoint.

## Changes on this branch

- Preserve the existing per-username PostgreSQL login limit (8 attempts per 15
  minutes) and add a second atomic, shared limit of 40 sign-in attempts per
  15-minute interval for the *observed ASGI peer* across different usernames.
- Persist HMAC identifiers for account and peer buckets, keyed by the application
  session signing secret; do not persist raw IP addresses or supplied passwords.
- Reject pilot login with a 503 if the ASGI client address is absent.
- Never parse arbitrary user-provided `X-Forwarded-For` headers in application
  code. Proxy trust must be explicitly configured and verified on the deployed
  infrastructure. In particular, a shared Railway proxy address is **not**
  sufficient evidence of unique client-IP enforcement.
- Tests cover multi-username throttling, missing peer information and attempted
  forwarding-header spoofing.
- Optional `TRUST_RAILWAY_EDGE_CLIENT_IP=true` uses Railway's documented
  `X-Real-IP` header (strictly one valid IPv4 or IPv6 address) for the source
  bucket instead of the shared ASGI reverse-proxy peer. The default is
  `false`. **Only enable this in the Railway HTTPS public-edge deployment
  with no untrusted bypass path.** The flag does not authenticate the header
  by itself: private callers within the Railway project are part of this
  trust boundary. Documented header: https://docs.railway.com/networking/public-networking/specs-and-limits

## Mandatory steps before a usable public demo

1. Observe the actual `request.client.host` behavior under Railway's ingress
   with two independent test clients and make sure spoofed forwarding headers
   cannot change it. **Do not log/store identifiable client IPs** just to test:
   use temporary source-token hashes or aggregate a per-client test result
   through a controlled diagnostic.
2. When using Railway HTTP ingress, configure and verify `TRUST_RAILWAY_EDGE_CLIENT_IP=true` and confirm spoofed public `X-Real-IP` is replaced by the edge. Put a trusted edge/WAF/IP-level rate limit in front of pilot login and
   expensive endpoints. The in-application peer limit is defense in depth, not
   a substitute for edge controls, particularly if all traffic appears to
   originate from one Railway proxy.
3. Confirm TLS, HTTP security headers, origin policy, allowed hostnames,
   request size limits, and no public PostgreSQL networking.
4. Provision two *synthetic-only* accounts using the migration-owner CLI,
   one borrower and one operator. Deliver one-time credentials privately;
   never put passwords, API keys or DSNs in git, CI output or this handoff.
5. Run a real-browser HTTPS walkthrough: borrower login, consent/evaluation,
   read-only explanation, explicit single-use approval, worker reconciliation,
   operator safe-vs-unsafe experiment, wrong-role denial, logout, mobile layout.
6. Test auth concurrency and edge-IP throttling, configure retention,
   backups/restore and resource/budget alerts. Keep real lending integrations
   and real customer data disabled.
7. Observe passing checks on the *exact* new commit before attaching any
   public Railway domain. Document the resulting public URL and observed tests.
8. If security review cannot be completed, publish only a static non-sensitive
   project overview while keeping the stateful pilot backend private.

## Cost constraint

The user does not want to subscribe to paid Railway weekly scheduling.
No new recurring Railway jobs or paid automations are required for this
checkpoint. The existing `verity-private-health-probe` schedule is every
15 minutes, not weekly; changing or deleting it should be a separate
cost/monitoring decision, not an accidental part of public release.

## Honest product boundary

Verity is an engineering portfolio demonstration with illustrative INR
figures, synthetic people and simulated lenders. Its results are *not* loan
offers, financial advice, approval, external-service exactly-once guarantees,
or evidence of regulatory certification.


## Deployment record — 26 November 2026 (India)

- GitHub PR: https://github.com/Riyamahato2003/ai-lending-reliability-lab/pull/7
- Exact tested + deployed source SHA: `8e981672d036535cec98e176bb02c42a99cba6ca`.
- GitHub Actions at that SHA: backend CI, container smoke, and isolated
  private-staging workflows all completed successfully.
- Railway API deployment ID: `071215db-db08-4e45-be25-27b817d33324`,
  status `SUCCESS`; `verity-api` remained in existing staging environment
  with Postgres/worker/migrations unchanged.
- A Railway-generated HTTPS hostname was attached:
  `https://verity-api-staging.up.railway.app` (service domain ID
  `dfc8dc6e-b7f7-413e-bc48-3f8d338aa8e7`, target container port 8000).
- This hostname being registered **does not prove outside-internet readiness**:
  external DNS/browser checks from the assistant's execution environment were
  inconclusive because that environment cannot resolve even ordinary public
  DNS. Railway's domain status returned a known hostname but no confirmed
  certificate/verification status at the time of inspection.
- Pilot self-signup remains disabled and the app must remain synthetic-only.
  Independently verify public TLS, ingress header integrity, abuse limits,
  browser sign-in and borrower/operator workflows before inviting testers.
- No extra Railway paid schedules or recurring jobs were created.


## External HTTP evidence (same public hostname)

Railway's public HTTP request stream subsequently recorded the following
responses, without needing to log client IPs:

| Method | Path | Status |
| --- | --- | --- |
| GET | `/` | `200` |
| GET | `/assets/index-CTRwSZU1.css` | `200` |
| GET | `/assets/index-BuBYqIIw.js` | `200` |
| GET | `/auth/capabilities` | `200` |
| GET | `/favicon.svg` | `200` |
| POST | `/pilot/sessions` | `401` |

A separate public HTTPS POST connectivity check to `/ready` returned
`405 Method Not Allowed` (expected for its GET-only route). These
observations support that the public hostname routes to the running
application and serves its frontend. They are **not** a complete browser,
login, TLS-certificate-chain, security or financial-workflow acceptance test.
The `401` does not establish a successful pilot login.

Remaining next step: securely provision and privately deliver two synthetic
pilot identities, then perform actual browser-based borrower and operator
journeys, permission boundary checks, mobile testing and ingress rate-limiting
validation. Never use real borrower data.
