from decimal import Context, Decimal, ROUND_HALF_UP, localcontext

from hypothesis import given, strategies as st

from lending_lab.policies import LENDERS, build_lender_payload, calculate_offer, compare_results, evaluate


PROFILE = {"monthly_income_minor": 420000, "monthly_debt_minor": 80000,
           "requested_amount_minor": 2400000, "term_months": 24,
           "employment_months": 36, "region": "synthetic-west", "internal_note": "never transmit"}


def test_amortization_independent_golden_vectors():
    # Expected values were calculated independently using the documented paise recurrence.
    assert calculate_offer(10001, 3, 0, "cedar")["payment_schedule_minor"] == [3334, 3334, 3333]
    positive = calculate_offer(100000, 12, 1200, "cedar")
    assert positive["payment_schedule_minor"] == [8885] * 11 + [8884]
    assert positive["total_interest_minor"] == 6619
    assert positive["total_repayment_minor"] == 106619


def test_adapters_use_explicit_nonidentical_allowlists():
    payloads = {name: build_lender_payload(name, PROFILE) for name in LENDERS}
    for name, payload in payloads.items():
        assert set(payload) == set(LENDERS[name]["fields"])
        assert "region" not in payload and "internal_note" not in payload
    assert set(payloads["harbor"]) != set(payloads["cedar"])


def test_versioned_rule_trace_and_offer_disclaimer():
    result = evaluate("cedar", PROFILE)
    assert result["policy_version"] == "synthetic-policy-2026-01"
    assert all(rule["rule_id"].startswith("cedar.") for rule in result["rules"])
    assert result["illustrative_only"] is True
    assert result["offer"]["is_real_offer"] is False


def test_three_lenders_apply_distinct_rules_documents_and_offer_policies():
    results = [evaluate(name, PROFILE) for name in LENDERS]
    assert all(result["eligible_under_demo_rules"] for result in results)
    assert len({tuple(result["documents"]) for result in results}) == 3
    rule_sets = {result["lender_id"]: {item["rule_id"].split(".", 1)[1] for item in result["rules"]}
                 for result in results}
    assert "employment_minimum" in rule_sets["harbor"]
    assert "debt_ratio_maximum" in rule_sets["cedar"]
    assert rule_sets["cedar"] != rule_sets["harbor"]
    assert len({result["offer"]["annual_rate_bps"] for result in results}) == 3


def test_comparison_orders_by_total_repayment_then_lender_id():
    tied = [
        {"lender_id": "zeta", "offer": {"total_repayment_minor": 100}},
        {"lender_id": "alpha", "offer": {"total_repayment_minor": 100}},
        {"lender_id": "not-eligible"},
    ]
    assert [r["lender_id"] for r in compare_results(tied)] == ["alpha", "zeta", "not-eligible"]


@given(
    principal=st.integers(min_value=1, max_value=10_000_000_000),
    months=st.integers(min_value=1, max_value=360),
    rate=st.integers(min_value=0, max_value=5000),
)
def test_offer_money_is_integer_and_bounded(principal, months, rate):
    offer = calculate_offer(principal, months, rate, "cedar")
    assert all(isinstance(offer[k], int) for k in ("monthly_payment_minor", "final_payment_minor", "total_interest_minor", "total_repayment_minor"))
    assert offer["total_interest_minor"] >= 0
    assert offer["total_repayment_minor"] == sum(offer["payment_schedule_minor"])
    assert offer["total_repayment_minor"] == principal + offer["total_interest_minor"]
    assert offer["final_payment_minor"] == offer["payment_schedule_minor"][-1]
    expected = int((Decimal(principal) * Decimal(rate) / Decimal(120000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    assert offer["period_interest_minor"] == expected


@given(
    principal=st.integers(min_value=1, max_value=10_000_000),
    months=st.integers(min_value=1, max_value=360),
    rate=st.integers(min_value=0, max_value=5000),
)
def test_amortization_final_payment_absorbs_minor_unit_residual(principal, months, rate):
    offer = calculate_offer(principal, months, rate, "cedar")
    assert sum(offer["payment_schedule_minor"]) == principal + offer["total_interest_minor"]
    assert offer["payments_count"] <= months
    balance = principal
    total_interest = 0
    monthly_rate = Decimal(rate) / Decimal(120000)
    for idx, payment in enumerate(offer["payment_schedule_minor"]):
        interest = int((Decimal(balance) * monthly_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        total_interest += interest
        if idx == len(offer["payment_schedule_minor"]) - 1:
            assert payment == balance + interest
        balance -= payment - interest
    assert balance == 0
    assert total_interest == offer["total_interest_minor"]


@given(
    principal=st.integers(min_value=1, max_value=100_000_000),
    rate=st.integers(min_value=0, max_value=5000),
)
def test_total_interest_non_decreasing_with_term(principal, rate):
    short = calculate_offer(principal, 12, rate, "cedar")
    long = calculate_offer(principal, 24, rate, "cedar")
    assert long["total_interest_minor"] >= short["total_interest_minor"]
