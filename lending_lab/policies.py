import hashlib
import json
from decimal import Context, Decimal, ROUND_HALF_UP, localcontext
from typing import Any


POLICY_VERSION = "synthetic-policy-2026-01"
OFFER_FORMULA_VERSION = "illustrative-offer-v1"

# Each policy has its own outbound field contract. No adapter receives the profile object.
LENDERS: dict[str, dict[str, Any]] = {
    "cedar": {"lender_version": "cedar-simulator-v1", "offer_version": "cedar-offer-2026-01",
              "fields": ("monthly_income_minor", "monthly_debt_minor", "requested_amount_minor", "term_months"),
              "min_income": 250000, "max_dti_bps": 4500, "max_amount": 5000000,
              "documents": ["income_statement"], "annual_rate_bps": 1250},
    "harbor": {"lender_version": "harbor-simulator-v1", "offer_version": "harbor-offer-2026-01",
               "fields": ("monthly_income_minor", "employment_months", "requested_amount_minor", "term_months"),
               "min_income": 200000, "min_employment": 12, "max_amount": 3500000,
               "documents": ["income_statement", "employment_history"], "annual_rate_bps": 1390},
    "summit": {"lender_version": "summit-simulator-v1", "offer_version": "summit-offer-2026-01",
               "fields": ("monthly_income_minor", "monthly_debt_minor", "requested_amount_minor", "term_months"),
               "min_income": 300000, "max_dti_bps": 3500, "max_amount": 8000000,
               "documents": ["income_statement", "debt_summary"], "annual_rate_bps": 1125},
}


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def build_lender_payload(lender_id: str, snapshot: dict[str, Any]) -> dict[str, int]:
    policy = LENDERS[lender_id]
    return {field: int(snapshot[field]) for field in policy["fields"]}


def evaluate(lender_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    policy = LENDERS[lender_id]
    payload = build_lender_payload(lender_id, snapshot)
    checks: list[dict[str, Any]] = []

    def check(rule: str, field: str, predicate: bool, observed: int, expected: str) -> None:
        checks.append({"rule_id": f"{lender_id}.{rule}", "field": field,
                       "observed": observed, "expected": expected,
                       "outcome": "pass" if predicate else "fail"})

    check("income_minimum", "monthly_income_minor", payload["monthly_income_minor"] >= policy["min_income"],
          payload["monthly_income_minor"], f">={policy['min_income']} minor units")
    check("amount_maximum", "requested_amount_minor", payload["requested_amount_minor"] <= policy["max_amount"],
          payload["requested_amount_minor"], f"<={policy['max_amount']} minor units")
    if "max_dti_bps" in policy:
        debt = payload["monthly_debt_minor"]
        income = payload["monthly_income_minor"]
        ratio_bps = (debt * 10000 // income) if income else 10000
        check("debt_ratio_maximum", "monthly_debt_minor", ratio_bps <= policy["max_dti_bps"],
              ratio_bps, f"<= {policy['max_dti_bps']} basis points of income")
    if "min_employment" in policy:
        check("employment_minimum", "employment_months", payload["employment_months"] >= policy["min_employment"],
              payload["employment_months"], f">={policy['min_employment']} months")

    eligible = all(item["outcome"] == "pass" for item in checks)
    result: dict[str, Any] = {"lender_id": lender_id, "eligible_under_demo_rules": eligible,
                              "documents": policy["documents"], "rules": checks,
                              "lender_version": policy["lender_version"],
                              "offer_version": policy["offer_version"],
                              "policy_version": POLICY_VERSION,
                              "data_sent": sorted(payload),
                              "illustrative_only": True}
    if eligible:
        result["offer"] = calculate_offer(payload["requested_amount_minor"], payload["term_months"],
                                           policy["annual_rate_bps"], lender_id)
    return result


def calculate_offer(principal_minor: int, term_months: int, annual_rate_bps: int, lender_id: str) -> dict[str, Any]:
    """Illustrative monthly amortization, in integer INR paise, with rounded per-period interest."""
    if principal_minor < 1 or term_months < 1 or annual_rate_bps < 0:
        raise ValueError("principal and term must be positive; rate must be nonnegative")
    with localcontext(Context(prec=50, rounding=ROUND_HALF_UP)):
        principal = Decimal(principal_minor)
        monthly_rate = Decimal(annual_rate_bps) / Decimal(120000)
        if monthly_rate == 0:
            payment = (principal / Decimal(term_months)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        else:
            factor = (Decimal(1) + monthly_rate) ** term_months
            payment = (principal * monthly_rate * factor / (factor - 1)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        first_interest = (principal * monthly_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        base_payment = max(int(payment), int(first_interest) + 1)

        balance = principal_minor
        total_interest = 0
        schedule: list[int] = []
        for period in range(term_months):
            interest = int((Decimal(balance) * monthly_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            due = balance + interest
            actual_payment = due if period == term_months - 1 else min(base_payment, due)
            schedule.append(actual_payment)
            total_interest += interest
            balance -= actual_payment - interest
            if balance <= 0:
                break
        if balance != 0:
            raise ArithmeticError("amortization schedule did not pay off within the stated term")
    total_repayment = sum(schedule)
    return {"currency": "INR", "principal_minor": principal_minor, "term_months": term_months,
            "currency_minor_unit": "paise", "annual_rate_bps": annual_rate_bps,
            "period_interest_minor": int(first_interest), "monthly_payment_minor": base_payment,
            "final_payment_minor": schedule[-1], "payments_count": len(schedule),
            "payment_schedule_minor": schedule, "total_interest_minor": total_interest,
            "total_repayment_minor": total_repayment,
            "comparison_key": [total_repayment, lender_id],
            "comparison_method": "sorted by estimated total repayment, then lender ID",
            "formula_version": OFFER_FORMULA_VERSION, "is_real_offer": False}


def compare_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eligible illustrative offers first by total repayment, then stable lender ID."""
    return sorted(results, key=lambda item: (
        0 if item.get("offer") else 1,
        item["offer"]["total_repayment_minor"] if item.get("offer") else 0,
        item["lender_id"],
    ))
