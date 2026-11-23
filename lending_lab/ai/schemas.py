from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExplainRequest(StrictModel):
    question: str = Field(min_length=1, max_length=1000)


class AIConsentRequest(StrictModel):
    consent_text: str = Field(min_length=20, max_length=2000)


class ToolName(StrEnum):
    STATUS = "get_workflow_status"
    RULE_TRACE = "explain_rule_trace"
    OFFERS = "compare_simulated_offers"
    CONSENT = "explain_consent_or_approval_requirements"


class Focus(StrEnum):
    GENERAL = "general"
    ELIGIBILITY = "eligibility"
    OFFER = "offer"
    SUBMISSION = "submission"
    APPROVAL = "approval"
    CONSENT = "consent"


class AssistantPlan(StrictModel):
    tool: ToolName
    focus: Focus


class WorkflowStatusInput(StrictModel):
    workflow_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


class RuleTraceInput(StrictModel):
    workflow_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


class OfferComparisonInput(StrictModel):
    workflow_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


class ConsentRequirementsInput(StrictModel):
    workflow_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


class EvidenceReference(StrictModel):
    kind: str
    identifier: str


class ExplanationResponse(StrictModel):
    answer: str
    tool_identifier: ToolName
    evidence_references: list[EvidenceReference]
    explanation_mode: str
    workflow_id: str
    uncertainty_flags: list[str]
    fallback_reason: str | None
    correlation_id: str
    simulated_data_disclaimer: str


class CapabilitiesResponse(StrictModel):
    modes: list[str]
    selected_mode: str
    external_ai_consent_required: bool
    external_ai_consent_active: bool
    tool_allowlist: list[str]
    actions_available: list[str]


class ConsentGrantResponse(StrictModel):
    purpose: str
    version: str
    consent_text: str
    grant_id: str | None
    status: str


class ToolEvidence(StrictModel):
    tool: ToolName
    facts: dict[str, Any]
    references: list[EvidenceReference]
    uncertainty_flags: list[str] = Field(default_factory=list)
