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
