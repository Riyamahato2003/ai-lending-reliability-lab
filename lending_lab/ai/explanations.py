"""Server-rendered factual language. Model text never becomes user-visible evidence."""
from lending_lab.ai.schemas import ToolEvidence, ToolName

DISCLAIMER = "Synthetic demo only: rule outcomes and offers are illustrative, not a real financial decision or offer."


def render_answer(evidence: ToolEvidence) -> tuple[str, list[str]]:
    f = evidence.facts
    if evidence.tool == ToolName.STATUS:
        state = f["state"]
        disposition = f["submission"]
        if disposition == "confirmed":
            ledger = f["ledger_evidence"]
            return (f"The workflow is {state}. The simulated lender ledger confirms application {ledger['application_id']} ({ledger['reference']}). This confirms only a simulated effect.", [])
        if disposition == "uncertain":
            return (f"The workflow is {state}, and the submission result is uncertain. I cannot confirm lender receipt from the available authoritative simulator evidence. A timeout or missing response is not a rejection.", ["submission_not_authoritatively_confirmed"])
        if state in {"consented", "evaluating", "compared", "awaiting_approval", "submitting"}:
            return (f"The workflow is {state}; no confirmed simulated lender submission is recorded.", [])
        return (f"The workflow is {state}. Its current status does not establish a lender-side effect.", [])
    if evidence.tool == ToolName.RULE_TRACE:
        if not f.get("available"):
            return (f["reason"], ["deterministic_evaluation_unavailable"])
        failing = [(l["lender_id"], r) for l in f["lenders"] for r in l["rule_trace"] if r["outcome"] == "fail"]
        if not failing:
            passed = [l["lender_id"] for l in f["lenders"] if l["eligible_under_demo_rules"]]
            return (f"Under illustrative policy {f['policy_version']}, the recorded rules pass for {', '.join(passed) or 'none of the evaluated lenders'}. This is not a real eligibility decision.", [])
        lender, rule = failing[0]
        return (f"Under policy {f['policy_version']}, lender {lender} did not pass rule {rule['rule_id']}: {rule['field']} was {rule['observed']}; the demo rule requires {rule['expected']}. The result is illustrative.", [])
    if evidence.tool == ToolName.OFFERS:
        offers = [o for o in f.get("offers", []) if not o["expired"]]
        if not offers:
            return ("No current unexpired simulated offer is available in this evaluation. The comparison order is " + f["comparison_order"] + ".", ["no_current_offer"])
        lowest = offers[0]
        amount = lowest["illustrative_values"]["total_repayment_minor"]
        return (f"The first unexpired offer in the original order is {lowest['lender_id']}, with estimated total repayment {amount} INR paise over {lowest['illustrative_values']['term_months']} months. Offers are {f['comparison_order']}; this is not a real offer.", [])
    lending = f.get("lending_consent")
    approval = f["approval"]
    consent_text = (f" Current lending consent wording (version {lending['text_version']}): {lending['text']}" if lending and lending.get("text") else "")
    return (f"Lending consent is {lending['status'] if lending else 'unavailable'}; external AI-processing consent is {f['external_ai_consent']['status']}. Approval status is {approval['status']}. {f['next_permitted_borrower_action']}{consent_text} The assistant cannot grant consent or approve or submit an application.", [])
