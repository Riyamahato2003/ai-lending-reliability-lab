"""Evidence selection and safe question routing; no raw financial snapshot is assembled."""
import re

from lending_lab.ai.schemas import AssistantPlan, Focus, ToolName


UNSAFE_PATTERNS = (
    r"\b(ignore|override|replace)\b.*\b(instructions|system|previous)\b",
    r"\b(approve|submit|send|change|update|delete|erase)\b.*\b(loan|application|database|audit|status|eligibility)\b",
    r"\b(show|give|reveal)\b.*\b(another|other)\b.*\b(borrower|customer|application)\b",
    r"\b(sql|select\s+\*|drop\s+table|internal tool|unlisted tool)\b",
    r"\b(invent|make up|lower)\b.*\b(rate|interest|offer|payment)\b",
    r"\b(can you|please)\b.*\b(submit|approve|revoke|grant consent)\b",
)


def is_action_or_injection(question: str) -> bool:
    return any(re.search(pattern, question, re.I | re.S) for pattern in UNSAFE_PATTERNS)


def deterministic_plan(question: str) -> AssistantPlan:
    q = question.lower()
    if any(k in q for k in ("consent", "withdraw", "revoke", "approval", "valid", "expired", "submit for me", "can you submit")):
        return AssistantPlan(tool=ToolName.CONSENT, focus=Focus.APPROVAL if "approv" in q or "valid" in q or "expired" in q else Focus.CONSENT)
    if any(k in q for k in ("offer", "repayment", "interest", "lowest", "cost", "payment")):
        return AssistantPlan(tool=ToolName.OFFERS, focus=Focus.OFFER)
    if any(k in q for k in ("eligible", "eligibility", "rule", "qualify", "ineligible")):
        return AssistantPlan(tool=ToolName.RULE_TRACE, focus=Focus.ELIGIBILITY)
    return AssistantPlan(tool=ToolName.STATUS, focus=Focus.SUBMISSION if any(k in q for k in ("lender", "received", "pending", "response", "lost", "application")) else Focus.GENERAL)
