# Checkpoint 3A verification

## Evidence boundary

This is an original portfolio demonstration with synthetic borrower records and simulated lenders. Verification uses PostgreSQL 16.16 on the local host. It does not prove exactly-once delivery to an independent lender, real-world credit eligibility, or production behavior. Checkpoints 3B (privacy deletion/revocation) and 3C (fault lab/metrics), Gemini, and the frontend were not implemented here.

## Acceptance traceability

| Acceptance criterion | Implementation | Exact test names | Status and observed evidence | Limitations |
|---|---|---|---|---|
| Approval stores immutable borrower/workflow, evaluation snapshot/digest, consent ID/purpose, policy/workflow versions, lender/offer versions, exact illustrative values, offer/approval expiry, single-use state and invalidation reason | `migrations/003_approvals_dispatch.sql`, `migrations/005_invalidated_approval_state.sql`, `lending_lab/approvals.py` | `test_approval_persists_full_immutable_binding_and_expiries`, `test_approval_explicit_authorization_owner_checks_and_reuse` | **Met**; PostgreSQL-backed tests pass | Key rotation invalidates approvals whose HMAC key version is no longer configured; external secret management is deployment responsibility. |
| Canonical approval payload follows documented constrained RFC 8785/JCS contract and versioned HMAC | `lending_lab/approvals.py`, `lending_lab/config.py` | `test_approval_payload_canonical_hash_vectors` | **Met**; fixed byte/HMAC golden vector passes; floats rejected | Canonicalizer intentionally accepts only this versioned schema's ASCII strings, safe integers, booleans, null, arrays and objects; it is not advertised as a general-purpose JCS library. |
| Server-authorized atomic single-use consumption; reject stale, expired, altered, reused approvals | `lending_lab/api.py`, `lending_lab/approvals.py`, migration 003 | `test_approval_consumption_is_atomic_single_use`, `test_approval_expiry_checked_at_consumption_and_dispatch`, `test_material_change_invalidates_approval`, `test_approval_explicit_authorization_owner_checks_and_reuse` | **Met**; concurrent consumer test permits exactly one success; expiry/material-change/replay cases pass | Real PostgreSQL concurrency test covers the application database, not an independent auth provider. |
| Approval and offer expiry checked at consumption and immediately before external request | `lending_lab/approvals.py`, `lending_lab/dispatch.py` | `test_approval_expiry_checked_at_consumption_and_dispatch` | **Met**; both expiry boundaries exercised | Time is injected through the project clock; PostgreSQL locking/transactions remain real. |
| Approval binding revalidated against snapshot, policy, consent, offer and workflow before dispatch | `lending_lab/approvals.py`, `lending_lab/dispatch.py` | `test_material_change_invalidates_approval`, `test_approval_explicit_authorization_owner_checks_and_reuse` | **Met**; changed policy/offer binding invalidates and no send occurs | Consent revocation orchestration and deletion are intentionally 3B. A new send checks active consent immediately before the external call. Reconciliation of a committed intent remains allowed after withdrawal and uses only its original key/payload and simulator ledger; cross-workflow revocation remains 3B. |
| Lender response matches immutable intent, documented response schema/status, derived application ID and authoritative simulator ledger | `lending_lab/dispatch.py`, `lending_lab/simulator.py` | `test_malformed_or_contradictory_lender_response_stays_unknown` (7 cases), `test_missing_authoritative_ledger_never_confirms` | **Met for simulator contract**; malformed, mismatched, incomplete, contradictory or unverified responses remain unresolved; ledger-backed recovery is tested | Simulator and application share PostgreSQL; this cannot validate a remote provider contract. |
| Dispatch intent (state, key, payload digest, immutable snapshot reference, lender and audit) commits before external I/O; confirmed result is persisted separately | `lending_lab/dispatch.py`, `lending_lab/workflows.py`, `migrations/003_approvals_dispatch.sql` | `test_dispatch_intent_commits_before_external_send`, `test_worker_process_crash_and_restart_has_no_duplicate_effect` | **Met**; sender observes committed intent; process crash before send leaves a recoverable intent | Simulator uses a PostgreSQL ledger in this same database server; no cross-system atomicity claim. |
| Interrupted evaluating, dispatching, and unknown workflows recover without converting uncertain outcomes into rejection | `lending_lab/dispatch.py`, `lending_lab/worker.py`, `lending_lab/workflows.py` | `test_restart_recovers_interrupted_evaluation`, `test_restart_reconciles_dispatching_and_unknown`, `test_worker_process_crash_and_restart_has_no_duplicate_effect` | **Met** for implemented recovery paths; process termination/restart cases pass at four interruption points | Only evaluation snapshots and simulator ledger are available as authorities in this demo; genuinely uncertain remote-provider outcomes stay unresolved. |
| Same lender ID, stable idempotency key and immutable payload used for reconciliation/retry; conflicting payload is surfaced for review | `lending_lab/dispatch.py`, `lending_lab/simulator.py`, `migrations/003_approvals_dispatch.sql` | `test_restart_reconciles_dispatching_and_unknown`, `test_stale_worker_idempotency_and_concurrent_double_submit`, `test_operator_conflict_review_is_restricted_and_immutable` | **Met** in simulator; same-payload replay, conflict, and review paths exercised | Does not establish exactly-once behavior against a real remote lender. Operators append evidence; original intent and ledger are immutable. |
| Actual simultaneous PostgreSQL queue claims use `SKIP LOCKED`; stale lease is fenced; concurrent double submit cannot create duplicate simulator effects | `lending_lab/queue.py`, `lending_lab/dispatch.py`, `migrations/001_initial.sql` | `test_concurrent_queue_claims_use_skip_locked`, `test_stale_worker_idempotency_and_concurrent_double_submit`, `test_stale_lease_is_rejected_before_send` | **Met**; tests coordinate worker threads/processes with barriers and use PostgreSQL | Concurrent test outcomes depend on nondeterministic scheduling; these are not repeatable seeded fault schedules. |
| Deterministic lock order and bounded database deadlock retry | `lending_lab/approvals.py`, `lending_lab/dispatch.py`, `lending_lab/db.py`, `migrations/004_lock_functions.sql` | `test_bounded_deadlock_retry_on_real_postgresql`, `test_approval_consumption_is_atomic_single_use` | **Met**; induced PostgreSQL deadlock is retried within the configured bound | Retries apply only to short rolled-back DB transactions (40P01/40001), never across external I/O. |
| Lease exceeds provider deadline plus result commit margin | `lending_lab/dispatch.py`, `lending_lab/queue.py` | `test_lease_budget_requires_provider_deadline_margin` | **Met**; 30-second lease exceeds 10-second simulated provider deadline plus 5-second margin | Fixed simulator timing; production provider latency needs a separately measured deadline/heartbeat strategy. |
| Audit meaningful approval, dispatch, recovery and resolution transitions; ordered and immutable | `lending_lab/db.py`, `lending_lab/approvals.py`, `lending_lab/dispatch.py`, migrations 001/003 | `test_every_allowed_state_transition_appends_matching_audit`, `test_approval_persists_full_immutable_binding_and_expiries`, `test_worker_process_crash_and_restart_has_no_duplicate_effect`, `test_operator_conflict_review_is_restricted_and_immutable` | **Met** in covered transitions; database trigger/permissions enforce append-only records and per-workflow order | Hash chain is tamper-evident against ordinary application edits, not tamper-proof against a database owner. |
| Operator review restricted by role/ownership; evidence is append-only and cannot mutate original intent/ledger | `lending_lab/api.py`, `lending_lab/dispatch.py`, `migrations/003_approvals_dispatch.sql` | `test_operator_conflict_review_is_restricted_and_immutable` | **Met**; borrower/wrong-role access denied; resolution evidence is separate | No operator UI; restricted API only. |
| Preserve Checkpoint 2 real PostgreSQL regression coverage and require tests in CI | `.github/workflows/ci.yml`, `scripts/assert_ci_test_results.py`, `tests/test_postgres.py`, `tests/test_database_security.py`, `tests/test_auth.py`, `tests/test_policies.py` | C2 required names include `test_cross_borrower_access_denied`, `test_runtime_role_cannot_modify_events_or_remove_audit_triggers`, `test_migrations_and_seed_are_reproducible`, `test_every_allowed_state_transition_appends_matching_audit`; 3A names listed above | **Met locally**; final JUnit assertion confirms 63 tests, zero failures/errors/skips, and required test cases present | Hosted GitHub Actions was not triggered or observed; Docker unavailable. YAML parsing is not a hosted CI run. |

## Test execution

Executed against PostgreSQL **16.16 (Homebrew)** at `127.0.0.1:5432`, with migration-owner, runtime, and lab/operator DSNs. Command:

```sh
APP_MODE=local_demo REQUIRE_POSTGRES_TESTS=1 \
TEST_DATABASE_URL=postgresql://lending_runtime:runtime-test-password@127.0.0.1:5432/lending_lab_review_verify \
TEST_MIGRATION_DATABASE_URL=postgresql://lending_migrator@127.0.0.1:5432/lending_lab_review_verify \
TEST_LAB_DATABASE_URL=postgresql://lending_lab_operator:lab-test-password@127.0.0.1:5432/lending_lab_review_verify \
.venv/bin/pytest -q --junitxml=/tmp/ai-lending-3a-audit-final.xml
```

Following audit corrections, the complete PostgreSQL suite was rerun and observed **63 passed, 0 failed, 0 skipped**, 1 upstream Starlette deprecation warning, 12.49 seconds. JUnit recorded 63 tests in `/tmp/ai-lending-3a-audit-final.xml`. An earlier full run exposed a faulty ledger-missing test fixture; after correcting the fixture, the full rerun passed. No test skips were accepted because `REQUIRE_POSTGRES_TESTS=1` was set.

Then executed:

```sh
.venv/bin/python scripts/assert_ci_test_results.py /tmp/ai-lending-3a-audit-final.xml
.venv/bin/python -m compileall -q lending_lab tests scripts
git diff --check
```

Observed: `.venv/bin/python scripts/assert_ci_test_results.py /tmp/ai-lending-3a-audit-final.xml` passed: `tests=63 failures=0 errors=0 skipped=0`, with required database tests present. Compilation passed with `.venv/bin/python -m compileall -q lending_lab tests scripts`; staged whitespace and secret checks are recorded below. PyYAML parsed `.github/workflows/ci.yml` and `docker-compose.yml` successfully. PostgreSQL version query returned `16.16 (Homebrew)`.

Docker verification: **unavailable** (`docker: command not found`), so Compose startup/config resolution and container behavior are unverified. Git remote inspection returned no configured remotes; no hosted CI run or URL is available. Local JUnit verification is not a CI run.

## Files changed for Checkpoint 3A

`PLAN.md`, `README.md`, `docker-compose.yml`, `scripts/assert_ci_test_results.py`, `lending_lab/api.py`, `lending_lab/config.py`, `lending_lab/db.py`, `lending_lab/policies.py`, `lending_lab/queue.py`, `lending_lab/schemas.py`, `lending_lab/workflows.py`, `lending_lab/approvals.py`, `lending_lab/dispatch.py`, `lending_lab/worker.py`, `migrations/003_approvals_dispatch.sql`, `migrations/004_lock_functions.sql`, `migrations/005_invalidated_approval_state.sql`, `tests/conftest.py`, `tests/test_postgres.py`, and `tests/test_checkpoint_3a.py`, and `docs/checkpoint-3a-verification.md`.

## Remaining correctness risks

- The simulator and application database share a PostgreSQL server. The simulator ledger is useful evidence for idempotency contracts and crash recovery but cannot establish exactly-once effects across a remote network/service.
- No live hosted CI or Docker Compose run was available.
- This checkpoint does not implement consent revocation/privacy deletion or the fault-injection measurement dashboard.
- PostgreSQL owner-level compromise can rewrite tamper-evident audit history; independent external anchoring is out of scope.
- No performance/load testing or multi-host deployment testing was performed.

Checkpoint 3A implementation is ready for review based on the evidence above. Checkpoint 3B/3C and later work remain out of scope; no subsequent checkpoint was started.


## Exact pytest collection inventory

Fresh command: `.venv/bin/pytest --collect-only -q`. Observed: **63 tests collected in 0.43s**. The inventory below is the complete pytest node-ID output, including parametrized cases. Seven response-validation variants and four process-crash interruption cases each count separately.

```text
tests/test_auth.py::test_expired_signed_session_is_rejected_with_injected_clock
tests/test_auth.py::test_tampered_session_signature_is_rejected_before_database_lookup
tests/test_auth.py::test_default_demo_secret_is_refused_outside_explicit_local_mode
tests/test_auth.py::test_local_demo_refuses_nonlocal_database_hosts
tests/test_checkpoint_3a.py::test_approval_payload_canonical_hash_vectors
tests/test_checkpoint_3a.py::test_approval_persists_full_immutable_binding_and_expiries
tests/test_checkpoint_3a.py::test_approval_consumption_is_atomic_single_use
tests/test_checkpoint_3a.py::test_approval_expiry_checked_at_consumption_and_dispatch
tests/test_checkpoint_3a.py::test_material_change_invalidates_approval
tests/test_checkpoint_3a.py::test_approval_explicit_authorization_owner_checks_and_reuse
tests/test_checkpoint_3a.py::test_dispatch_intent_commits_before_external_send
tests/test_checkpoint_3a.py::test_restart_reconciles_dispatching_and_unknown
tests/test_checkpoint_3a.py::test_malformed_or_contradictory_lender_response_stays_unknown[<lambda>-response_lender_mismatch]
tests/test_checkpoint_3a.py::test_malformed_or_contradictory_lender_response_stays_unknown[<lambda>-response_payload_hash_mismatch]
tests/test_checkpoint_3a.py::test_malformed_or_contradictory_lender_response_stays_unknown[<lambda>-response_schema_mismatch]
tests/test_checkpoint_3a.py::test_malformed_or_contradictory_lender_response_stays_unknown[<lambda>-unsupported_response_status0]
tests/test_checkpoint_3a.py::test_malformed_or_contradictory_lender_response_stays_unknown[<lambda>-invalid_simulated_application_id]
tests/test_checkpoint_3a.py::test_malformed_or_contradictory_lender_response_stays_unknown[<lambda>-simulated_application_id_mismatch]
tests/test_checkpoint_3a.py::test_malformed_or_contradictory_lender_response_stays_unknown[<lambda>-unsupported_response_status1]
tests/test_checkpoint_3a.py::test_missing_authoritative_ledger_never_confirms
tests/test_checkpoint_3a.py::test_revoked_consent_blocks_new_send_but_allows_existing_intent_reconciliation
tests/test_checkpoint_3a.py::test_stale_worker_idempotency_and_concurrent_double_submit
tests/test_checkpoint_3a.py::test_expired_worker_effect_is_reconciled_without_stale_lease_ack
tests/test_checkpoint_3a.py::test_concurrent_queue_claims_use_skip_locked
tests/test_checkpoint_3a.py::test_restart_recovers_interrupted_evaluation
tests/test_checkpoint_3a.py::test_bounded_deadlock_retry_on_real_postgresql
tests/test_checkpoint_3a.py::test_stale_lease_is_rejected_before_send
tests/test_checkpoint_3a.py::test_worker_process_crash_and_restart_has_no_duplicate_effect[before_intent_commit-81]
tests/test_checkpoint_3a.py::test_worker_process_crash_and_restart_has_no_duplicate_effect[after_intent_commit-82]
tests/test_checkpoint_3a.py::test_worker_process_crash_and_restart_has_no_duplicate_effect[after_lender_effect-83]
tests/test_checkpoint_3a.py::test_worker_process_crash_and_restart_has_no_duplicate_effect[during_result_persistence-84]
tests/test_checkpoint_3a.py::test_operator_conflict_review_is_restricted_and_immutable
tests/test_checkpoint_3a.py::test_lease_budget_requires_provider_deadline_margin
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

## Repeated PostgreSQL concurrency and recovery run

Configuration: PostgreSQL **16.16 (Homebrew)** at `127.0.0.1:5432`, disposable database `lending_lab_review_verify`, `APP_MODE=local_demo`, `REQUIRE_POSTGRES_TESTS=1`, separate migration-owner/runtime/lab DSNs. A Python driver launched **50 independent pytest subprocesses** against final code. Each ran 13 cases: atomic approval consumption and expiry, intent-before-send, restart reconciliation, actual concurrent double-submit and queue claims, bounded deadlock retry, stale lease rejection, effect reconciliation after lease expiry, and four worker crash/restart points.

Observed aggregate: **50 executions; 650 passed; 0 failed; 0 skipped** (13 cases each, 336.4 seconds). Every subprocess exited 0; no intermittent failures were observed. Separately, all seven response-validation parametrizations passed in a targeted run (**7 passed, 0 failed, 0 skipped**), and the complete suite passed 63 cases.

An earlier pre-final stress batch ran 50 processes with a broader 20-case set (1,000 passed, no failures/skips, approximately 409.8 seconds) before the application-ID-derived response check and its extra test variant were added. It is retained as historical evidence; the 650-case aggregate above is the repeated run on final code. The previously observed intermittent crash-lock-release failure and its bounded polling adjustment are documented above. Repeated local runs do not prove all race interleavings are eliminated or establish exactly-once effects against an independent lender.

## Audit correction evidence

- Response validation checks exact response keys/types, expected lender ID, expected payload digest, documented acceptance status, ID shape and deterministic lender/key-derived ID, plus equality with the simulator ledger row. Invalid/contradictory responses remain `submission_unknown`; recovery resolves only from a matching ledger row.
- New PostgreSQL tests: `test_malformed_or_contradictory_lender_response_stays_unknown` (7 parametrized cases), `test_missing_authoritative_ledger_never_confirms`, `test_expired_worker_effect_is_reconciled_without_stale_lease_ack`, and `test_revoked_consent_blocks_new_send_but_allows_existing_intent_reconciliation`.
- Consent boundary: withdrawal after intent commit but before final send authorization blocks a new effect. Once the request is in flight, an effect that commits despite withdrawal remains uncertain if its response is lost; recovery can reconcile that committed intent without a new send. These tests exercise the boundary only; cross-workflow revocation and deletion remain Checkpoint 3B.
- Stale-worker outcome: an old lease cannot acknowledge a replacement lease. The replacement reconciles the existing effect and completes the queue step; one simulator effect and one `submission.confirmed` audit event are observed.
