"""Fail CI if required PostgreSQL checks are absent or any tests were skipped."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


REQUIRED_TESTS = {
    "test_cross_borrower_access_denied",
    "test_runtime_role_cannot_modify_events_or_remove_audit_triggers",
    "test_migrations_and_seed_are_reproducible",
    "test_every_allowed_state_transition_appends_matching_audit",
    "test_approval_payload_canonical_hash_vectors",
    "test_approval_consumption_is_atomic_single_use",
    "test_approval_expiry_checked_at_consumption_and_dispatch",
    "test_material_change_invalidates_approval",
    "test_dispatch_intent_commits_before_external_send",
    "test_restart_reconciles_dispatching_and_unknown",
    "test_bounded_deadlock_retry_on_real_postgresql",
    "test_stale_worker_idempotency_and_concurrent_double_submit",
    "test_concurrent_queue_claims_use_skip_locked",
    "test_worker_process_crash_and_restart_has_no_duplicate_effect",
    "test_operator_conflict_review_is_restricted_and_immutable",
    "test_revocation_after_intent_blocks_send_and_recovery_never_retries_without_effect",
    "test_revoke_while_request_is_in_flight_prevents_simulated_effect",
    "test_each_fault_scenario_compares_safe_and_unsafe_on_same_schedule",
    "test_revocation_fault_interactions_never_resend_safely_and_show_unsafe_violation",
    "test_seeded_replay_has_same_logical_results_and_metadata",
    "test_invalid_experiment_configurations_are_rejected_before_persistence",
    "test_operator_authorization_and_lab_role_database_isolation",
    "test_lab_records_contain_no_borrower_payload_values",
}


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: assert_ci_test_results.py JUNIT_XML")
    root = ET.parse(Path(sys.argv[1])).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    # Pytest appends [parameter-id] to parametrized test names in JUnit XML.
    names = {case.attrib.get("name", "").split("[", 1)[0] for case in root.iter("testcase")}
    missing = sorted(REQUIRED_TESTS - names)
    print(f"CI verification: tests={tests} failures={failures} errors={errors} skipped={skipped}")
    if missing:
        print(f"Missing required PostgreSQL test cases: {', '.join(missing)}")
    if tests == 0 or failures or errors or skipped or missing:
        return 1
    print("CI verification assertion passed: required database tests ran and no tests skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
