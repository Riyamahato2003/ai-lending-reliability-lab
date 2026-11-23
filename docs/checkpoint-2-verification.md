# Checkpoint 2 Verification Matrix

## Current verification run

The final run used a newly initialized UTF-8 PostgreSQL 16.16 cluster on `127.0.0.1:55432`, a disposable database named `lending_lab_final_verify`, and separate migration-owner, runtime, and lab credentials. Python was 3.13.15. The test fixture applied migrations and deterministic borrower fixtures before exercising the PostgreSQL tests. Seeded password hashes contain fresh salts, so password-digest bytes differ between seed runs; seeded identities and borrower profile data remain stable.

Command executed:

```sh
APP_MODE=local_demo REQUIRE_POSTGRES_TESTS=1 \
TEST_DATABASE_URL=postgresql://lending_runtime:runtime-test-password@127.0.0.1:55432/lending_lab_final_verify \
TEST_MIGRATION_DATABASE_URL=postgresql://lending_migrator@127.0.0.1:55432/lending_lab_final_verify \
TEST_LAB_DATABASE_URL=postgresql://lending_lab_operator:lab-test-password@127.0.0.1:55432/lending_lab_final_verify \
.venv/bin/pytest -q --junitxml=/tmp/ai-lending-pytest.xml
.venv/bin/python scripts/assert_ci_test_results.py /tmp/ai-lending-pytest.xml
```

Observed output: **34 passed, 0 failed, 0 skipped, 1 upstream Starlette deprecation warning**. The separate CI assertion printed `CI verification: tests=34 failures=0 errors=0 skipped=0` and `CI verification assertion passed: required database tests ran and no tests skipped`.

Other observed checks:

- `.venv/bin/pytest --collect-only -q` — **34 tests collected**; the complete collected test-name listing is below.
- `.venv/bin/python -m compileall -q lending_lab tests scripts` — exit 0.
- Python YAML parse of `.github/workflows/ci.yml` and `docker-compose.yml` — both parsed.
- `git diff --check` — exit 0 before staging; staged whitespace check is recorded after staging.
- With all three test DSNs unset and `REQUIRE_POSTGRES_TESTS` unset, `.venv/bin/pytest -q` — **12 passed, 22 skipped**. The skips are PostgreSQL tests and are not counted as integration evidence.
- With DSNs unset and `REQUIRE_POSTGRES_TESTS=1`, a PostgreSQL test failed during setup with `CI requires TEST_DATABASE_URL, TEST_MIGRATION_DATABASE_URL, and TEST_LAB_DATABASE_URL` (1 setup error, exit 1). This confirms a required database job cannot silently skip because its DSNs are missing.

The first hardening run failed because a manually initialized SQL_ASCII cluster made PostgreSQL `char` results arrive as bytes and the newly authored positive-rate golden vector was incorrect. That cluster was stopped; a fresh UTF-8 PostgreSQL cluster was initialized, the trigger-state assertion was made tolerant of psycopg's `char` representation, and the independent vector was corrected. The final complete run above passed. These are real intermediate failures, not omitted pass claims.

## Collection discrepancy

The previously recorded baseline had **28 collected test functions**, while the matrix contained **27 unique `test_*` names**. The discrepancy was one omitted matrix reference: `test_correlation_id_is_server_issued_and_cancellation_is_allowed`. The collected functions were ordinary named tests; there was no pytest parametrization that accounted for the difference. Hypothesis executes generated examples inside a test function; they do not appear as separate collected test names. The old row saying “all 28 tests” was therefore not a traceable exact list. The current suite has 34 collected test functions after adding six cases; the final collection output is included here.

## Current collected test names

```text
tests/test_auth.py::test_expired_signed_session_is_rejected_with_injected_clock
tests/test_auth.py::test_tampered_session_signature_is_rejected_before_database_lookup
tests/test_auth.py::test_default_demo_secret_is_refused_outside_explicit_local_mode
tests/test_auth.py::test_local_demo_refuses_nonlocal_database_hosts
tests/test_database_security.py::test_runtime_role_cannot_modify_events_or_remove_audit_triggers
tests/test_database_security.py::test_lab_database_role_cannot_read_or_modify_borrower_profiles
tests/test_database_security.py::test_auth_failure_is_logged_without_credentials
tests/test_database_security.py::test_security_event_log_is_append_only
tests/test_database_security.py::test_cross_borrower_denial_is_logged
tests/test_database_security.py::test_wrong_role_operator_action_is_logged
tests/test_database_security.py::test_operator_lab_activity_is_logged_and_separate_from_workflow_audit
tests/test_database_security.py::test_logout_revokes_session
tests/test_database_security.py::test_server_side_user_role_change_invalidates_old_session
tests/test_database_security.py::test_operator_token_cannot_use_borrower_routes
tests/test_policies.py::test_amortization_independent_golden_vectors
tests/test_policies.py::test_adapters_use_explicit_nonidentical_allowlists
tests/test_policies.py::test_versioned_rule_trace_and_offer_disclaimer
tests/test_policies.py::test_three_lenders_apply_distinct_rules_documents_and_offer_policies
tests/test_policies.py::test_comparison_orders_by_total_repayment_then_lender_id
tests/test_policies.py::test_offer_money_is_integer_and_bounded
tests/test_policies.py::test_amortization_final_payment_absorbs_minor_unit_residual
tests/test_policies.py::test_total_interest_non_decreasing_with_term
tests/test_postgres.py::test_cross_borrower_access_denied
tests/test_postgres.py::test_state_machine_rejects_illegal_and_stale_transitions
tests/test_postgres.py::test_correlation_id_is_server_issued_and_cancellation_is_allowed
tests/test_postgres.py::test_audit_immutable_and_sequences_ordered
tests/test_postgres.py::test_simulator_ledger_idempotency_and_payload_conflict
tests/test_postgres.py::test_submission_unknown_reconciliation_reuses_key
tests/test_postgres.py::test_versioned_evaluation_snapshot_is_immutable
tests/test_postgres.py::test_migrations_and_seed_are_reproducible
tests/test_postgres.py::test_postgres_step_claim_and_lease_token
tests/test_postgres.py::test_expired_queue_lease_is_reclaimable_with_clock
tests/test_postgres.py::test_queue_retry_backoff_and_dead_letter_after_max_attempts
tests/test_postgres.py::test_every_allowed_state_transition_appends_matching_audit
```

## Requirement traceability

| # | Checkpoint 2 acceptance criterion | Implementation | Exact tests | Status and gaps |
|---|---|---|---|---|
| 1 | Compose uses real PostgreSQL and separate migration/runtime/lab roles; migrations and synthetic fixtures are reproducible. | [docker-compose.yml](../docker-compose.yml), [role bootstrap](../docker/postgres/init/10-roles.sh), [migrations](../migrations/001_initial.sql), [migrate.py](../lending_lab/migrate.py), [conftest.py](../tests/conftest.py) | `test_migrations_and_seed_are_reproducible` | **Unverified** as a full Compose acceptance: database migrations/seeds ran on real PostgreSQL with three roles, but Docker is unavailable and Compose startup/health/order were not observed. |
| 2 | Runtime cannot alter audit records or remove protected triggers; lab DB role cannot read/modify borrower data. | [security migration](../migrations/002_database_roles_and_security_events.sql), [database security tests](../tests/test_database_security.py) | `test_runtime_role_cannot_modify_events_or_remove_audit_triggers`; `test_lab_database_role_cannot_read_or_modify_borrower_profiles` | **Met** on real PostgreSQL. Runtime DML/DDL attempts were denied; migration owner confirmed protected triggers remained enabled. |
| 3 | Demo sessions are signed, expiring, revocable, borrower/role bound; owner and role checks apply to routes. | [auth.py](../lending_lab/auth.py), [api.py](../lending_lab/api.py), [credentials.py](../lending_lab/credentials.py), [auth tests](../tests/test_auth.py), [security tests](../tests/test_database_security.py), [route tests](../tests/test_postgres.py) | `test_expired_signed_session_is_rejected_with_injected_clock`; `test_tampered_session_signature_is_rejected_before_database_lookup`; `test_cross_borrower_access_denied`; `test_cross_borrower_denial_is_logged`; `test_wrong_role_operator_action_is_logged`; `test_operator_token_cannot_use_borrower_routes`; `test_server_side_user_role_change_invalidates_old_session`; `test_logout_revokes_session`; `test_default_demo_secret_is_refused_outside_explicit_local_mode`; `test_local_demo_refuses_nonlocal_database_hosts` | **Met** for the implemented demo routes and startup policy. Auth throttling is explicitly not implemented; planned parameters and tests are specified in PLAN. This is not production authentication. |
| 4 | State machine rejects illegal/stale transitions; uncertain submission is not rejection and reconciliation reuses the same key. | [workflows.py](../lending_lab/workflows.py), [simulator.py](../lending_lab/simulator.py), [state tests](../tests/test_postgres.py) | `test_state_machine_rejects_illegal_and_stale_transitions`; `test_submission_unknown_reconciliation_reuses_key`; `test_simulator_ledger_idempotency_and_payload_conflict`; `test_every_allowed_state_transition_appends_matching_audit` | **Met** for state graph and recovery primitive. No write-ahead dispatch/restart worker is implemented; those remain 3A. |
| 5 | Evaluation snapshots are immutable, versioned, and produce deterministic rule-level explanations from real synthetic inputs. | [workflows.py](../lending_lab/workflows.py), [policies.py](../lending_lab/policies.py), [migration](../migrations/001_initial.sql) | `test_versioned_evaluation_snapshot_is_immutable`; `test_versioned_rule_trace_and_offer_disclaimer` | **Met**. No AI-authored explanation is used. |
| 6 | Three distinct simulated lender adapters apply separate rules/documents and outbound field allowlists. | [policies.py](../lending_lab/policies.py) | `test_adapters_use_explicit_nonidentical_allowlists`; `test_three_lenders_apply_distinct_rules_documents_and_offer_policies` | **Met** for synthetic adapters only; no real lender integration. |
| 7 | Simulator ledger replays same key/payload and conflicts on same key/different payload. | [simulator.py](../lending_lab/simulator.py), [migration](../migrations/001_initial.sql) | `test_simulator_ledger_idempotency_and_payload_conflict`; `test_submission_unknown_reconciliation_reuses_key` | **Met** against PostgreSQL simulator ledger. It shares the application's PostgreSQL server and does not prove exactly-once effects against an independent provider. |
| 8 | Workflow audit is ordered, immutable at DB layer, and tampering is detectable. | [audit migration](../migrations/001_initial.sql), [security migration](../migrations/002_database_roles_and_security_events.sql), [db.py](../lending_lab/db.py) | `test_audit_immutable_and_sequences_ordered`; `test_runtime_role_cannot_modify_events_or_remove_audit_triggers`; `test_every_allowed_state_transition_appends_matching_audit` | **Met** for tested paths. Hashing is tamper-evident, not tamper-proof against a database owner able to change data and functions. |
| 9 | Durable PostgreSQL queue uses `SKIP LOCKED`, leases, recovery eligibility, bounded attempts and dead-letter state. | [queue.py](../lending_lab/queue.py), [clock.py](../lending_lab/clock.py), [migration](../migrations/002_database_roles_and_security_events.sql) | `test_postgres_step_claim_and_lease_token`; `test_expired_queue_lease_is_reclaimable_with_clock`; `test_queue_retry_backoff_and_dead_letter_after_max_attempts` | **Met** for sequential lease/retry semantics on PostgreSQL. Simultaneous worker interleaving tests and automated crash recovery are deferred. |
| 10 | Illustrative loan calculations define rounding/payoff and comparison labels; properties and golden vectors run. | [policies.py](../lending_lab/policies.py), [policy tests](../tests/test_policies.py) | `test_amortization_independent_golden_vectors`; `test_offer_money_is_integer_and_bounded`; `test_amortization_final_payment_absorbs_minor_unit_residual`; `test_total_interest_non_decreasing_with_term`; `test_comparison_orders_by_total_repayment_then_lender_id` | **Met** for implemented arithmetic. Decimal context is explicit; amounts are integer INR paise; final residual pays balance to zero. Values are illustrative, never preapproval or a real offer. |
| 11 | Security events separately record authentication failures, cross-borrower denials, wrong roles, logout and lab administration without credentials. | [auth.py](../lending_lab/auth.py), [api.py](../lending_lab/api.py), [db.py](../lending_lab/db.py), [security tests](../tests/test_database_security.py) | `test_auth_failure_is_logged_without_credentials`; `test_security_event_log_is_append_only`; `test_cross_borrower_denial_is_logged`; `test_wrong_role_operator_action_is_logged`; `test_operator_lab_activity_is_logged_and_separate_from_workflow_audit`; `test_logout_revokes_session` | **Met** for present events. Fault-injection administration does not exist yet. |
| 12 | Required PostgreSQL integration tests cannot silently skip in CI; CI and Docker status are evidence-based. | [conftest.py](../tests/conftest.py), [assertion script](../scripts/assert_ci_test_results.py), [workflow](../.github/workflows/ci.yml) | `test_migrations_and_seed_are_reproducible`; `test_runtime_role_cannot_modify_events_or_remove_audit_triggers`; JUnit assertion requires `test_cross_borrower_access_denied`, `test_runtime_role_cannot_modify_events_or_remove_audit_triggers`, `test_migrations_and_seed_are_reproducible`, and `test_every_allowed_state_transition_appends_matching_audit` | **Local suite met**: 34/34 passed, no skips, explicit JUnit assertion passed; missing-DSN negative check failed as required. **CI run unverified**: no remote or GitHub run. **Docker unverified**: Docker executable unavailable. |
| 13 | Role changes on the server invalidate stale sessions; DB connection identity is separate from borrower/operator identity. | [auth.py](../lending_lab/auth.py), [config.py](../lending_lab/config.py), [database roles](../migrations/002_database_roles_and_security_events.sql) | `test_server_side_user_role_change_invalidates_old_session`; `test_operator_token_cannot_use_borrower_routes`; `test_default_demo_secret_is_refused_outside_explicit_local_mode` | **Met** in the demo authorization model. Database connection remains the restricted runtime role regardless of token role. |
| 14 | Approval and 3A/3B/3C privacy/concurrency/reliability contracts are specified without claiming implementation. | [PLAN.md](../PLAN.md) | No implementation tests by design; planned 3A/3B/3C test names and gates are specified in PLAN | **Contract documented; implementation not started.** Approval consumption, approval table, revocation, privacy deletion, dispatch recovery, and Reliability Lab metrics are not Checkpoint 2 capabilities. |

## 3A contract and checkpoint boundaries

[PLAN.md](../PLAN.md) now specifies the planned approvals table, RFC 8785 canonical payload, approval bindings and expiry checks at both consumption and dispatch, one-use atomic consumption, invalidation after material changes, database lock order/deadlock retries, distinct `submitting` and `dispatching` meanings, write-ahead dispatch, lease/stale-worker behavior, reconciliation, and unresolved conflict handling. These are **3A contracts only**, not implemented. It also specifies 3B consent-version/privacy/revocation behavior and 3C operator-only fault schedules, unsafe baseline, safety-violation counts, recovery/stuck-workflow measurements, and reproducibility metadata. Do not infer implementation from the test names listed as future gates.

## Unverified conditions and limits

- Docker and Docker Compose startup are unverified: `docker` is not installed in this workspace. YAML parsing does not establish container health.
- GitHub Actions is unverified: the workflow has not been triggered or observed, so there is no run link. The workflow now requires all three DSNs and checks the JUnit report for skipped tests and required database cases.
- No Git remote is configured. No push was attempted.
- No true concurrent queue worker race, external lender effect, automated crash recovery, approval path, consent revocation, AI/Gemini integration, frontend, or Playwright journey is included in Checkpoint 2.
- Authentication throttling is specified as a future hardening requirement but is not implemented or tested.
- Reliability metrics/fault harness are not implemented. Zero observed failures are not proof of 100% reliability.
- A simulator ledger in the same database is a teaching simulation, not an independent external-effect guarantee.

## Current collection output summary

The exact output was `34 tests collected in 0.41s` on the final pre-commit collection run. The names are recorded above. The prior matrix's 27-vs-28 discrepancy is historical and does not indicate parametrization.
