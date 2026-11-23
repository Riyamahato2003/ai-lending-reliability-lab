# Checkpoint 3B verification

**Scope:** direct purpose-specific consent revocation, sensitive synthetic-data deletion, re-consent boundaries, and preservation of recovery evidence. Checkpoint 3C, Gemini, and frontend work are out of scope.

## Requirement-to-test traceability

| Requirement | Implementation | PostgreSQL tests | Result / limitation |
|---|---|---|---|
| Immutable, versioned consent wording and purpose-bound keyed HMAC | `migrations/006_consent_privacy.sql`, `lending_lab/privacy.py`, `lending_lab/api.py` | `tests/test_postgres.py::test_state_machine_rejects_illegal_and_stale_transitions` (asserts grant HMAC/version binding); `tests/test_checkpoint_3b.py::test_revocation_before_approval_and_after_approval_invalidates_unused_approval` | Met in the exercised fixed-purpose demo. The consent catalog currently has a seeded illustrative purpose and wording; this is not a general consent-management product. |
| Direct borrower revocation, owner checks, and wrong-role rejection | `lending_lab/api.py`, `lending_lab/consent.py` | `tests/test_checkpoint_3b.py::test_revocation_authorization_owner_and_operator_boundaries` | Met for borrower, other-borrower, and operator principals. |
| Grant lock before sorted workflow, approval, and step locks; atomically stop unclaimed work and invalidate unsent approvals | `lending_lab/consent.py`, `migrations/004_lock_functions.sql`, `migrations/006_consent_privacy.sql` | `tests/test_checkpoint_3b.py::test_revocation_before_approval_and_after_approval_invalidates_unused_approval`; `tests/test_checkpoint_3b.py::test_revocation_spans_all_workflows_for_same_grant_and_preserves_audit` | Met for tested PostgreSQL transactions. Concurrent race evidence is finite and does not cover every possible interleaving. |
| Dispatch/reconciliation boundary: no new effect after revocation; committed intent can still reconcile from original key, lender, digest, and simulator ledger after sensitive deletion | `lending_lab/dispatch.py`, `lending_lab/simulator.py`, `lending_lab/consent.py` | `tests/test_checkpoint_3a.py::test_revoked_consent_blocks_new_send_but_allows_existing_intent_reconciliation`; `tests/test_checkpoint_3b.py::test_revocation_after_intent_blocks_send_and_recovery_never_retries_without_effect` | Met for simulator-ledger recovery. The repeated exact race suite below covers the valid existing test. The simulator is local PostgreSQL, not a remote lender. |
| Revocation immediately before send and during an in-flight request | `lending_lab/dispatch.py`, `lending_lab/simulator.py`, `lending_lab/consent.py` | `tests/test_checkpoint_3a.py::test_revoked_consent_blocks_new_send_but_allows_existing_intent_reconciliation`; `tests/test_checkpoint_3b.py::test_revoke_while_request_is_in_flight_prevents_simulated_effect` | Met for these controlled simulator races. A genuinely remote request cannot be recalled once accepted; status must be reconciled. |
| Revocation after a lender effect but before response, then recovery without borrower payload | `lending_lab/dispatch.py`, `lending_lab/consent.py` | `tests/test_checkpoint_3a.py::test_revoked_consent_blocks_new_send_but_allows_existing_intent_reconciliation` | Met: the test commits the ledger effect, revokes during the callback, loses the response, deletes sensitive rows, and verifies recovery confirms from the original ledger result. |
| No resend after revocation when ledger has no effect | `lending_lab/dispatch.py`, `lending_lab/simulator.py` | `tests/test_checkpoint_3b.py::test_revocation_after_intent_blocks_send_and_recovery_never_retries_without_effect` | Met: verifies no sender call, no ledger row, and unresolved status remains. |
| Cross-workflow revocation and audit continuity | `lending_lab/consent.py`, `lending_lab/db.py` | `tests/test_checkpoint_3b.py::test_revocation_spans_all_workflows_for_same_grant_and_preserves_audit`; `tests/test_checkpoint_3b.py::test_revocation_after_intent_blocks_send_and_recovery_never_retries_without_effect` | Met for workflows sharing a grant; verifies audit chain after sensitive deletion. Normal API creation issues a new grant per workflow, so the same-grant multi-workflow case is exercised through the underlying model. |
| Re-grant creates new identifiers and requires explicit re-entry after deletion | `lending_lab/api.py`, `lending_lab/schemas.py` | `tests/test_checkpoint_3b.py::test_regrant_requires_new_grant_workflow_and_explicit_synthetic_inputs` | Met. Re-grant does not resume a revoked workflow. |
| Sensitive fields are separable and deleted; nonsensitive audit and immutable dispatch evidence remain | `migrations/006_consent_privacy.sql`, `lending_lab/consent.py` | `tests/test_checkpoint_3b.py::test_revocation_after_intent_blocks_send_and_recovery_never_retries_without_effect`; `tests/test_checkpoint_3b.py::test_revocation_spans_all_workflows_for_same_grant_and_preserves_audit` | Met for live PostgreSQL rows. Deletion does not erase retained backups, WAL archives, exports, or copies outside this database. No cryptographic-erasure guarantee is made. |
| Existing Checkpoint 2/3A behavior remains covered | Backend modules and migrations listed above | Complete pytest suite (69 collected cases) | 69 passed, 0 failed, 0 skipped in the observed run. Passing count is not proof of complete coverage. |

## Actual verification

PostgreSQL was **16.16 (Homebrew)**, queried from the test database. Python was **3.13.15**. The disposable database was `lending_lab_3b_verify`; tests used separate runtime, migrator, and lab roles. The DSNs were provided through `TEST_DATABASE_URL`, `TEST_MIGRATION_DATABASE_URL`, and `TEST_LAB_DATABASE_URL`; `REQUIRE_POSTGRES_TESTS=1` was set.

Full suite command (DSN values intentionally omitted from this report):

```sh
REQUIRE_POSTGRES_TESTS=1 \
  TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  TEST_MIGRATION_DATABASE_URL="$TEST_MIGRATION_DATABASE_URL" \
  TEST_LAB_DATABASE_URL="$TEST_LAB_DATABASE_URL" \
  .venv/bin/pytest -q
```

Observed result: **69 passed, 0 failed, 0 skipped** in 13.65 seconds. One Starlette/httpx deprecation warning was emitted. The command `.venv/bin/pytest --collect-only -q` observed `69 tests collected in 0.41s`. The suite contains the previously reported 63 cases plus 6 Checkpoint 3B cases; the complete test run itself observed 69 passing cases.

Race-sensitive repetition command ran these four PostgreSQL tests in one pytest invocation per iteration, for **10 independent iterations**:

- `tests/test_checkpoint_3a.py::test_revoked_consent_blocks_new_send_but_allows_existing_intent_reconciliation`
- `tests/test_checkpoint_3a.py::test_stale_worker_idempotency_and_concurrent_double_submit`
- `tests/test_checkpoint_3b.py::test_revocation_after_intent_blocks_send_and_recovery_never_retries_without_effect`
- `tests/test_checkpoint_3b.py::test_revoke_while_request_is_in_flight_prevents_simulated_effect`

Observed aggregate: **40 test executions, 40 passed, 0 failed, 0 skipped** (4 passed in each of 10 iterations). Each iteration emitted the same Starlette/httpx deprecation warning. Controlled barriers/events create overlap in the concurrency cases. These finite runs do not establish that all race interleavings are safe.

A prior focused invocation of `tests/test_checkpoint_3a.py tests/test_checkpoint_3b.py` observed **35 passed, 0 failed, 0 skipped** after test setup fixes. The final full suite is the authoritative complete-run result above.

`git diff --check` and `git diff --cached --check` both completed with no output. Docker verification was unavailable because the `docker` command is not installed. No GitHub Actions run was triggered or observed; CI status is **unverified**. No CI pass is claimed.

## Implementation boundaries and residual risks

- A revocation committed before the simulator creates a new ledger effect blocks that effect. If the effect committed first and the response was lost, the worker can reconcile it after revocation using the immutable intent metadata and ledger. The effect cannot be recalled.
- The live simulator ledger and application tables share PostgreSQL infrastructure. This demonstrates application recovery semantics, not exactly-once delivery to an independent lender.
- Revocation removes sensitive borrower, evaluation, lender-result, dispatch-payload, and approval payload rows. It retains consent version text, keyed digests, workflow/audit metadata, immutable dispatch key/digest metadata, and simulator ledger evidence needed for reconciliation.
- Database row deletion does not purge backups, WAL, snapshots, exports, logs, or host-level copies. Key lifecycle and backup purge are not proven; no irreversible erasure claim is made.
- Repeated concurrency runs are observations under this environment/configuration, not a proof over all schedules.
- Docker startup and hosted CI remain unverified.

No Checkpoint 3C implementation was started.
