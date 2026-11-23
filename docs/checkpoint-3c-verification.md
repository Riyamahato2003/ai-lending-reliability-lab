# Checkpoint 3C verification

**Status:** implementation is ready for independent review, with the limits below. This report records workspace observations, not an independent review or hosted CI run for the current uncommitted changes.

## Architecture and isolation

The operator-only API is implemented in `lending_lab/api.py` with typed request/response models in `lending_lab/schemas.py` and experiment behavior in `lending_lab/reliability_lab.py`:

- `GET /operator/lab/scenarios` lists the fixed scenarios.
- `POST /operator/lab/runs` runs a bounded `safe` or `unsafe` experiment.
- `GET /operator/lab/runs/{run_id}` returns run metadata and per-trial outcomes.
- `POST /operator/lab/compare` compares completed safe and unsafe runs with equivalent schedule configuration.

The request model rejects unknown fields, constrains seeds to nonnegative signed 64-bit integers, limits trials to 1–100, and allows the revocation toggle only for the two supported scenarios. The API caps experiment/compare JSON bodies at 16 KiB and permits one synchronous run per API process. This semaphore is process-local, not a distributed concurrency limit.

`lending_lab_operator` uses only `lab_runs`, `lab_events`, `lab_dispatch_intents`, `lab_simulator_ledger`, and `lab_effects`, plus the narrow security-event append function. Each trial checks that the actual PostgreSQL user is the lab role and has no table privileges on borrower, consent, evaluation, workflow, approval, submission, or application simulator tables. Database-role tests attempt reads/writes against protected tables and verify runtime-role denial on lab tables. Lab tables have immutable evidence triggers; the lab role cannot mutate run configuration, events, intent, or ledger rows. These tables share the PostgreSQL cluster with application data, but the database grants and foreign-key graph isolate the lab role and experiment rows from borrower/workflow records. The lab harness does not invoke borrower workflow routes or use borrower payload values.

Every run is first committed as `running`. In safe mode, each immutable `lab_dispatch_intents` row commits in its own PostgreSQL transaction before trial processing. The following trial transaction runs the modeled effect and its recovery logic together atomically; this is deliberately not a simulated external transaction boundary. The unsafe baseline has no intent row. The per-trial outcome event commits afterward in another transaction, and the final aggregate is committed after all trials. A process interruption leaves a partial run inspectable as `running`; there is no run-resume endpoint, and the operator should start a new run rather than treating partial metrics as complete.

### Safe and unsafe definitions

The safe lab model uses write-ahead intent, a stable per-trial key, a unique ledger key scoped to run/trial, consent checks before new effects, shared confirmation-shape validation, authoritative lab-ledger reconciliation, and immutable intent bindings. It is a focused model in isolated lab tables; it does not execute the production application workflow code end to end.

The deliberately unsafe teaching baseline intentionally omits write-ahead intent, persistent idempotency, consent rechecks, authoritative reconciliation, response verification, deadline/lease fencing, and immutable intent binding. Its duplicate/unauthorized/unverified outcomes are observed defects in this constructed baseline only. They are not measured defects in the safe application or in a real lender system.

## Fault scenarios

| Scenario | Injected behavior | Logical schedule |
|---|---|---|
| `REQUEST_LOST_BEFORE_LENDER` | The first request is lost before the simulator effect; the safe path retries the same key only if consent remains active. | No first effect; retry/revocation decision is deterministic. |
| `EFFECT_COMMITTED_RESPONSE_LOST` | The simulator ledger/effect commits, then the response is lost. Safe recovery reconciles the original key; the unsafe baseline blindly retries. | Ledger effect precedes response loss. |
| `DUPLICATE_RESPONSE` | The same confirmation is delivered twice for one provider attempt. | No additional lender request is implied by the duplicate delivery. |
| `CONFLICTING_RESPONSE` | Confirmation payload hash contradicts the immutable intent and ledger. | Safe path rejects the response then reconciles; unsafe model mutates its toy binding and trusts it. |
| `RESPONSE_AFTER_DEADLINE` | A fixed logical response is marked late at 150 ms against a 100 ms provider deadline. | No real sleep or provider timing is performed; safe path reconciles the ledger. |

Optional revocation interaction commits revocation before recovery in the request-lost case and after the first effect in the effect-committed case. The safe path can reconcile an already committed effect after revocation but cannot retry a no-effect request after revocation. The deterministic harness has no threads/processes; PostgreSQL concurrency tests separately exercise nondeterministic interleavings.

## Metrics

For each run, the API persists a run UUID, implementation, scenario, seed, trial count, provider-attempt count, synthetic effects created, effects confirmed by authoritative ledger evidence, workflows the implementation marked submitted, duplicate effects, unauthorized effects, unverified confirmations, recovery/unresolved counts, recovery steps/duration, invariant violation event counts, violating-trial counts, execution environment, configuration and hash, commit SHA/dirty marker, timestamps, and experiment version.

- `effects_created` counts rows in the isolated synthetic effect ledger.
- `confirmed_effects` counts only workflows whose result the safe model validated against authoritative ledger evidence. The unsafe baseline cannot increase this value without such evidence.
- `workflows_marked_submitted` records the implementation's claimed submitted outcomes, including unsafe false claims; `unverified_confirmations` and `no_confirmation_without_authoritative_evidence` expose those claims.
- `attempt_count` counts provider attempts, including a request lost before reaching the lender; a blocked retry is not an attempt.
- `duplicate_effects` is `max(effects_created for the trial - 1, 0)` summed across trials. `unauthorized_effects` counts effect rows made while the synthetic consent flag is false. An effect committed before revocation is not post-revocation.
- Invariant violations are counts of violation events. `violation_trials` is the numerator for the explicit denominator `trials`; rates are observations over that finite sample. A zero observed count is not a universal reliability guarantee.
- `recovery_steps` counts post-unknown retry-authorization, retry, or ledger-reconciliation decisions. `recovery_duration_ms` is measured using local monotonic wall time around recovery logic only; it is not provider latency. Wall time is nondeterministic and not part of replay equality.
- A configuration hash binds the logical schedule including implementation. Safe/unsafe comparison removes that implementation field and returns a shared schedule hash. The run records its own configuration hash. `commit_sha` uses `uncommitted:<HEAD>` when the working tree is dirty, or `unknown` if Git identity cannot be read.

The fixed logical schedule is reproducible for code, configuration, seed, scenario, and trial count. It does not reproduce database thread/process scheduling. The harness does not calculate confidence intervals; no statistical confidence claim is made.

## Requirement-to-test traceability

| Acceptance criterion | Implementation | Exact test(s) | Result |
|---|---|---|---|
| Five named fault schedules are visible and safely handled; same schedule comparison exposes baseline defects. | `lending_lab/reliability_lab.py`, `lending_lab/api.py` | `test_each_fault_scenario_compares_safe_and_unsafe_on_same_schedule[REQUEST_LOST_BEFORE_LENDER]`, `[EFFECT_COMMITTED_RESPONSE_LOST]`, `[DUPLICATE_RESPONSE]`, `[CONFLICTING_RESPONSE]`, `[RESPONSE_AFTER_DEADLINE]` | Met in exercised deterministic cases. |
| Safe write-ahead intent is committed before a trial can create a synthetic effect; unsafe has no intent. | `migrations/007_reliability_lab.sql`, `lending_lab/reliability_lab.py` | The five parameter cases above assert safe intent count, unsafe intent count, and effect count. | Met for lab transactions tested on PostgreSQL. This is a simulator-only transaction boundary. |
| Committed effect / response loss reconciles by original key; unsafe duplicate is measured, not asserted as a safe-system defect. | `lending_lab/reliability_lab.py` | `test_each_fault_scenario_compares_safe_and_unsafe_on_same_schedule[EFFECT_COMMITTED_RESPONSE_LOST]` | Met: sample below observed safe 5 effects/5 authoritative confirmations; unsafe 10 effects/5 duplicates/5 unverified claims. |
| Duplicate, contradictory, and late response cases do not become unsupported safe confirmations; uncertainty remains separately represented. | `lending_lab/reliability_lab.py`, `lending_lab/dispatch.py` | `test_each_fault_scenario_compares_safe_and_unsafe_on_same_schedule[DUPLICATE_RESPONSE]`, `[CONFLICTING_RESPONSE]`, `[RESPONSE_AFTER_DEADLINE]` | Met for deterministic modeled responses. No real remote provider response was tested. |
| Consent withdrawal blocks new safe effects but existing effects can reconcile. | `lending_lab/reliability_lab.py` | `test_revocation_fault_interactions_never_resend_safely_and_show_unsafe_violation` | Met for the two supported schedule placements; full 3B tests cover production simulator boundaries separately. |
| Same-seed replay repeats logical outcomes and idempotency keys; environment/config/run identity is present. | `lending_lab/reliability_lab.py` | `test_seeded_replay_has_same_logical_results_and_metadata` | Met for tested logical outcomes; runtime durations and run UUIDs intentionally differ. |
| Trial bounds, seed, scenario, revocation combinations, extra fields, and body size are validated before run persistence. | `lending_lab/schemas.py`, `lending_lab/api.py` | `test_invalid_experiment_configurations_are_rejected_before_persistence` | Met for tested boundary values. The 100-trial upper limit and 16 KiB body cap are local resource bounds, not a DoS defense for a public service. |
| Only operator principal can use lab routes; borrower and runtime DB role are denied lab data; lab role is denied application data. | `lending_lab/api.py`, `lending_lab/db.py`, `migrations/007_reliability_lab.sql` | `test_operator_authorization_and_lab_role_database_isolation` | Met for exercised API roles and actual PostgreSQL grants. The database remains a shared cluster. |
| Lab records contain no borrower payload values; safety metrics use explicit numerator and denominator. | `lending_lab/reliability_lab.py`, migration 007 | `test_lab_records_contain_no_borrower_payload_values`; five parameter cases assert violation numerator and trial denominator consistency. | Met for tested fields and schedules. |
| CI assertion requires PostgreSQL cases and rejects skips. | `scripts/assert_ci_test_results.py`, `.github/workflows/ci.yml` | Full JUnit XML assertion command below | Passed locally for all required test names; no hosted C3 run observed. |

The five scenario IDs are pytest parameter cases under one parametrized function. `tests/test_reliability_lab.py` contributes 10 collected cases: five scenario parameters plus five other test functions. The existing Checkpoint 2–3B regression suite contributes 69 cases, for **79 total collected cases**. Actual collection output was `79 tests collected in 0.43s` from `pytest --collect-only -q`.

## Actual experiment sample

The API was invoked against the disposable local PostgreSQL database as the seeded operator. This is an observed run, not generated example data. Environment: Python 3.13.15, PostgreSQL 16.16 (Homebrew), macOS; code identity `uncommitted:7c197794e932378fe5a48d6513bef996b01c4dd6`. Both runs used scenario `EFFECT_COMMITTED_RESPONSE_LOST`, seed `2468`, 5 trials, logical schedule hash `f81e029230b6c04d30ab20b23ee142046929387e1e0889c289803cce4f5d8797`.

| Metric | Safe | Deliberately unsafe |
|---|---:|---:|
| Provider attempts | 5 | 10 |
| Synthetic effects created | 5 | 10 |
| Authoritatively confirmed effects | 5 | 0 |
| Workflows marked submitted | 5 | 5 |
| Duplicate effects | 0 | 5 |
| Unverified confirmations | 0 | 5 |
| Recovered workflows | 5 | 0 |
| Unresolved workflows | 0 | 0 |
| Violating trials for duplicate-effect invariant | 0 / 5 | 5 / 5 |
| Violating trials for unsupported confirmation invariant | 0 / 5 | 5 / 5 |

Run IDs: safe `37af918c-1fb3-4812-ae6f-d814be5d1fa7`; unsafe `27f1ee27-765e-4634-ba06-3a8179cdcd48`. The sample shows behavior of the two isolated lab implementations under one matching deterministic schedule; it does not establish production reliability or external-lender behavior.

### Five-scenario observed comparison

The API was then run for every named scenario, with safe and unsafe runs compared at identical scenario/seed/trial settings (**2 trials each**). These are actual experiment records in the same disposable PostgreSQL database, not calculated estimates. Format for outcomes is `attempts / effects / authoritative confirmations / duplicates / recovered / unresolved`.

| Scenario | Seed | Safe outcomes | Unsafe outcomes | Unsafe invariant violations |
|---|---:|---|---|---|
| `REQUEST_LOST_BEFORE_LENDER` | 5201 | 4 / 2 / 2 / 0 / 2 / 0 | 4 / 2 / 0 / 0 / 0 / 0 | 2 unsupported confirmations; 2 timeout-to-rejection collapses |
| `EFFECT_COMMITTED_RESPONSE_LOST` | 5202 | 2 / 2 / 2 / 0 / 2 / 0 | 4 / 4 / 0 / 2 / 0 / 0 | 2 unsupported confirmations; 2 duplicate effects |
| `DUPLICATE_RESPONSE` | 5203 | 2 / 2 / 2 / 0 / 0 / 0 | 2 / 2 / 0 / 0 / 0 / 0 | 2 unsupported confirmations; both runs observed 2 duplicate response deliveries |
| `CONFLICTING_RESPONSE` | 5204 | 2 / 2 / 2 / 0 / 2 / 0 | 2 / 2 / 0 / 0 / 0 / 0 | 2 unsupported confirmations; 2 immutable-binding mutations |
| `RESPONSE_AFTER_DEADLINE` | 5205 | 2 / 2 / 2 / 0 / 2 / 0 | 2 / 2 / 0 / 0 / 0 / 0 | 2 unsupported confirmations; both runs observed 2 late responses |

In every safe run, every invariant violation count was zero; all runs recorded zero unauthorized effects, two workflows marked submitted, and zero unresolved workflows. In unsafe runs, `confirmed_effects` remained zero because none had authoritative lab-ledger confirmation; `workflows_marked_submitted` was two and `unverified_confirmations` was two in each case. Unsafe violations are outcomes of the intentionally unsafe teaching implementation. They are not independent evidence of a defect in the production dispatch worker. These five two-trial samples are finite and do not establish universal reliability.

Run IDs (safe / unsafe): request lost `d92a06e6-d057-4e47-a076-24c6f5b1cc94` / `d4487948-59a4-4db0-9558-81eeade81774`; effect/response lost `ac0262fc-5d40-4f4a-b020-a60c190fff11` / `f84259dd-7be5-4de9-a632-c44a9f36727c`; duplicate response `1e057c57-8ac6-4353-a390-31314ee0fc0b` / `a107aff5-31a6-4489-9552-7b090a3e1a93`; conflicting response `7417d093-ba4c-4535-aae4-c8ec0025c89c` / `fb205333-b279-4efa-b9d4-4d4b28581b58`; late response `5b8186b2-7c50-42ca-82c0-b70f5ccd18f9` / `879c3845-e293-4916-8228-97e4c273496a`.

## PostgreSQL verification and commands

A new database `lending_lab_3c_final_verify` was created with owner `lending_migrator`. Before migration it had **0 public tables**. PostgreSQL reported **16.16 (Homebrew)**. Tests used distinct migration-owner, runtime, and lab DSNs. The DSNs and credentials are intentionally omitted here.

Full suite command:

```sh
REQUIRE_POSTGRES_TESTS=1 APP_MODE=local_demo \
  TEST_DATABASE_URL="$RUNTIME_TEST_DSN" \
  TEST_MIGRATION_DATABASE_URL="$MIGRATION_TEST_DSN" \
  TEST_LAB_DATABASE_URL="$LAB_TEST_DSN" \
  .venv/bin/pytest -q --junitxml=/tmp/ai-lending-3c-final.xml
.venv/bin/python scripts/assert_ci_test_results.py /tmp/ai-lending-3c-final.xml
```

Observed final result: **79 passed, 0 failed, 0 skipped**, 1 Starlette/httpx deprecation warning. The assertion printed `tests=79 failures=0 errors=0 skipped=0` and `CI verification assertion passed: required database tests ran and no tests skipped`. The 6-case focused Checkpoint 3B suite also passed **6/6** on the prior fresh disposable database during final 3B reproduction. Migration 007 was exercised from scratch on the final fresh database.

During implementation, the first focused lab run observed **4 passed and 6 failed**; the failure pattern exposed that per-run aggregates/completion were finalized inside the trial loop. Code inspection also found that implicit connection transactions would make nested transactions savepoints rather than separately committed intent/effect boundaries. The code was corrected to aggregate after all trials and use autocommit connections with explicit transactions. The focused lab suite then passed 10/10, and the final full suite above passed against migration 007 applied to a new empty database.

Focused repeated command selected `tests/test_reliability_lab.py` plus:

- `tests/test_checkpoint_3a.py::test_stale_worker_idempotency_and_concurrent_double_submit`
- `tests/test_checkpoint_3a.py::test_expired_worker_effect_is_reconciled_without_stale_lease_ack`
- `tests/test_checkpoint_3a.py::test_concurrent_queue_claims_use_skip_locked`
- `tests/test_checkpoint_3a.py::test_bounded_deadlock_retry_on_real_postgresql`
- `tests/test_checkpoint_3b.py::test_revocation_after_intent_blocks_send_and_recovery_never_retries_without_effect`
- `tests/test_checkpoint_3b.py::test_revoke_while_request_is_in_flight_prevents_simulated_effect`

The final-code repeated sample ran **5 independent pytest executions**, **16 passed per execution**, **80 passed total, 0 failed, 0 skipped**. Each execution launched a separate pytest process against the same explicitly disposable test database; fixtures reset test tables each time. The concurrency cases use PostgreSQL barriers/worker coordination. Results are finite observations, not proof over all schedules.

`.venv/bin/python -m compileall -q lending_lab tests scripts` and `git diff --check` completed successfully. `pytest --collect-only -q` collected 79 tests. Docker Compose could not be run because `docker` is not installed. Hosted GitHub Actions for the pushed Checkpoint 3C commit `d5bb414d5a846aa08b1ba309a438d280ce72418c` completed successfully: [run 45145267495](https://github.com/Riyamahato2003/ai-lending-reliability-lab/actions/runs/45145267495). It observed 79 passed, 0 failures, 0 skips and the explicit JUnit assertion passed. GitHub emitted an Actions Node.js 20 deprecation warning; it did not fail the workflow. The older Checkpoint 3B run is [45129430058](https://github.com/Riyamahato2003/ai-lending-reliability-lab/actions/runs/45129430058).

## Limitations and remaining risks

- The experiment simulator uses a local PostgreSQL lab ledger; it does not model network partitions or prove idempotency at an independent lender.
- The safe lab model shares selected validation helpers but is not an end-to-end invocation of the production dispatch state machine. Its deterministic outcomes are not a substitute for the 3A/3B PostgreSQL tests.
- `RESPONSE_AFTER_DEADLINE` is a logical fixed schedule; the harness does not sleep or measure a real provider deadline. Recovery durations measure local computation only.
- The run guard is one-per-process, and the lab uses the same database cluster with role/table isolation rather than a separate PostgreSQL service. No production security boundary is claimed.
- A killed experiment leaves an incomplete `running` record and partial immutable trial data. There is no resume/delete/cleanup API; manual maintenance would require migration-owner review.
- Wall-clock timestamps/durations differ across repeat runs. PostgreSQL thread scheduling is nondeterministic and is not controlled by the experiment seed.
- No statistical confidence interval is reported. Zero observed violations in finite samples do not imply 100% reliability.
- Docker remains unverified. Hosted CI passed for the recorded commit above; the independent reviewer’s full database reproduction has not been performed in this workspace task.
- The project remains synthetic and illustrative; no real borrower, lender, financial effect, regulatory status, or exact-once external guarantee is involved.
