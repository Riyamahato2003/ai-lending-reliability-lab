from lending_lab.ai.schemas import AssistantPlan, Focus, ToolEvidence, ToolName

ALLOWED_FOCI = {
    ToolName.STATUS: {Focus.GENERAL, Focus.SUBMISSION},
    ToolName.RULE_TRACE: {Focus.GENERAL, Focus.ELIGIBILITY},
    ToolName.OFFERS: {Focus.GENERAL, Focus.OFFER},
    ToolName.CONSENT: {Focus.GENERAL, Focus.CONSENT, Focus.APPROVAL},
}


def validate_plan(plan: AssistantPlan) -> AssistantPlan:
    if plan.focus not in ALLOWED_FOCI[plan.tool]:
        raise ValueError("tool and focus are inconsistent")
    return plan


def validate_evidence(evidence: ToolEvidence, workflow_id: str) -> ToolEvidence:
    evidence = ToolEvidence.model_validate(evidence.model_dump())
    for ref in evidence.references:
        if not ref.identifier or len(ref.identifier) > 200:
            raise ValueError("invalid evidence reference")
    if evidence.tool == ToolName.STATUS:
        if evidence.facts.get("workflow_id") != workflow_id:
            raise ValueError("status workflow does not match request")
        if evidence.facts.get("submission") == "confirmed":
            ledger = evidence.facts.get("ledger_evidence")
            if not isinstance(ledger, dict) or ledger.get("confirmed") is not True:
                raise ValueError("confirmation lacks authoritative simulator evidence")
    if evidence.tool == ToolName.OFFERS:
        if evidence.facts.get("comparison_order") != "sorted by estimated total repayment, then lender ID":
            raise ValueError("offer comparison label is invalid")
        for offer in evidence.facts.get("offers", []):
            if offer.get("illustrative_values", {}).get("is_real_offer") is not False:
                raise ValueError("offer is not explicitly illustrative")
    if evidence.tool == ToolName.RULE_TRACE:
        for lender in evidence.facts.get("lenders", []):
            if any(rule.get("outcome") not in {"pass", "fail"} for rule in lender.get("rule_trace", [])):
                raise ValueError("rule trace has unsupported outcome")
    return evidence
