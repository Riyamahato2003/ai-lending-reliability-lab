from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Role(StrEnum):
    BORROWER = "borrower"
    OPERATOR = "operator"


class WorkflowState(StrEnum):
    DRAFT = "draft"
    CONSENTED = "consented"
    EVALUATING = "evaluating"
    COMPARED = "compared"
    AWAITING_APPROVAL = "awaiting_approval"
    SUBMITTING = "submitting"
    DISPATCHING = "dispatching"
    SUBMISSION_UNKNOWN = "submission_unknown"
    SUBMITTED = "submitted"
    INELIGIBLE = "ineligible"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CONSENT_REVOKED = "consent_revoked"


class SessionRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    role: Role
    borrower_id: str | None


class SyntheticFinancialDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monthly_income_minor: int = Field(gt=0, le=100_000_000)
    monthly_debt_minor: int = Field(ge=0, le=100_000_000)
    requested_amount_minor: int = Field(gt=0, le=100_000_000)
    term_months: int = Field(gt=0, le=120)
    employment_months: int = Field(ge=0, le=1200)
    region: str = Field(min_length=1, max_length=64)


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(pattern="^illustrative_offer_comparison$", max_length=64)
    consent_text: str = Field(min_length=20, max_length=2000)
    financial_details: SyntheticFinancialDetails | None = None


class WorkflowResponse(BaseModel):
    id: str
    borrower_id: str
    state: WorkflowState
    version: int
    policy_version: str
    consent_grant_id: str


class EvaluationResponse(BaseModel):
    workflow_id: str
    snapshot_id: str
    input_hash: str
    policy_version: str
    results: list[dict[str, Any]]


class TransitionRequest(BaseModel):
    target: WorkflowState
    expected_version: int = Field(ge=0)


class TransitionResponse(BaseModel):
    workflow_id: str
    state: WorkflowState
    version: int


class ApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lender_id: str = Field(pattern="^(cedar|harbor|summit)$")
    expected_version: int = Field(ge=0)
    approval_ttl_seconds: int = Field(default=300, ge=1, le=1800)


class ApprovalResponse(BaseModel):
    approval_id: str
    workflow_id: str
    lender_id: str
    offer_values: dict[str, Any]
    offer_expires_at: datetime
    approval_expires_at: datetime
    workflow_version: int
    status: str


class ApprovalConsumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_workflow_version: int = Field(ge=0)


class OperatorResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disposition: str = Field(pattern="^(remain_unresolved|evidence_recorded)$")
    evidence: str = Field(min_length=1, max_length=2000)


class LabFaultScenario(StrEnum):
    REQUEST_LOST_BEFORE_LENDER = "REQUEST_LOST_BEFORE_LENDER"
    EFFECT_COMMITTED_RESPONSE_LOST = "EFFECT_COMMITTED_RESPONSE_LOST"
    DUPLICATE_RESPONSE = "DUPLICATE_RESPONSE"
    CONFLICTING_RESPONSE = "CONFLICTING_RESPONSE"
    RESPONSE_AFTER_DEADLINE = "RESPONSE_AFTER_DEADLINE"


class LabImplementation(StrEnum):
    SAFE = "safe"
    UNSAFE = "unsafe"


class LabRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: LabFaultScenario
    implementation: LabImplementation
    seed: int = Field(strict=True, ge=0, le=(1 << 63) - 1)
    trials: int = Field(strict=True, ge=1, le=100)
    revoke_before_recovery: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_revocation_scenario(self):
        if self.revoke_before_recovery and self.scenario not in {
            LabFaultScenario.REQUEST_LOST_BEFORE_LENDER,
            LabFaultScenario.EFFECT_COMMITTED_RESPONSE_LOST,
        }:
            raise ValueError("revocation interaction is supported only for request-lost or effect-committed scenarios")
        return self


class LabCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    safe_run_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    unsafe_run_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


class LabRunResult(BaseModel):
    id: str
    implementation: LabImplementation
    scenario: LabFaultScenario
    seed: int
    trials: int
    attempt_count: int
    effects_created: int
    confirmed_effects: int
    workflows_marked_submitted: int
    duplicate_effects: int
    unauthorized_effects: int
    unverified_confirmations: int
    duplicate_response_deliveries: int
    late_responses: int
    recovered_workflows: int
    unresolved_workflows: int
    recovery_steps: int
    recovery_duration_ms: float
    invariant_violations: dict[str, int]
    violation_trials: dict[str, int]
    metrics: dict[str, Any]
    configuration: dict[str, Any]
    configuration_hash: str
    commit_sha: str
    environment: dict[str, str]
    started_at: datetime
    completed_at: datetime | None
    run_status: str
    experiment_version: str
    deterministic_schedule: bool
    trial_outcomes: list[dict[str, Any]] = Field(default_factory=list)


class LabScenarioInfo(BaseModel):
    label: LabFaultScenario
    description: str


class LabCompareResult(BaseModel):
    scenario: LabFaultScenario
    seed: int
    trials: int
    schedule_hash: str
    safe_run: LabRunResult
    unsafe_run: LabRunResult
    safe_minus_unsafe: dict[str, int]
    comparison_note: str


class ErrorResponse(BaseModel):
    detail: str
    correlation_id: str
