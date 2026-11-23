"""Evidence-grounded explanation orchestration with external-consent fencing."""
import time

from fastapi import HTTPException

from lending_lab.ai.adapter import GeminiProvider
from lending_lab.ai.context import deterministic_plan, is_action_or_injection
from lending_lab.ai.explanations import DISCLAIMER, render_answer
from lending_lab.ai.mock_provider import MockProvider
from lending_lab.ai.schemas import AssistantPlan, ExplanationResponse, ToolName
from lending_lab.ai.tools import TOOL_INPUTS, TOOL_REGISTRY
from lending_lab.ai.validation import validate_evidence, validate_plan
from lending_lab.config import get_settings
from lending_lab.db import append_security_event, connect, transaction
from lending_lab.privacy import consent_text_hmac

AI_PURPOSE = "ai_explanation_processing"


def _provider(mode: str):
    settings = get_settings()
    if mode == "mock":
        return MockProvider()
    if mode == "gemini":
        if not settings.gemini_api_key or not settings.gemini_model:
            raise RuntimeError("missing_credentials")
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model,
                              settings.gemini_timeout_seconds)
    raise ValueError("deterministic mode has no provider")


def _classify(question: str, mode: str, borrower_id: str) -> tuple[AssistantPlan, str, str | None]:
    """Return plan, reported mode, fallback. Gemini sees only question and only under locked grant."""
    if is_action_or_injection(question):
        return deterministic_plan("consent approval action"), mode, "protected_action_or_untrusted_instruction"
    if mode == "deterministic":
        return deterministic_plan(question), mode, None
    if mode == "mock":
        try:
            return validate_plan(_provider(mode).classify(question)), mode, None
        except Exception:
            return deterministic_plan(question), "deterministic", "mock_provider_invalid_output"
    if mode != "gemini":
        return deterministic_plan(question), "deterministic", "invalid_provider_mode"
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        return deterministic_plan(question), "deterministic", "gemini_credentials_or_model_missing"
    try:
        # The AI grant row lock is held only for this bounded outbound request. A direct
        # revoke blocks here and commits after any already-started request completes;
        # after it commits, later requests observe revoked and use local fallback.
        with transaction() as conn:
            grant = conn.execute(
                "SELECT id,status FROM consent_grants WHERE borrower_id=%s AND purpose=%s AND status='active' FOR UPDATE",
                (borrower_id, AI_PURPOSE),
            ).fetchone()
            if not grant:
                return deterministic_plan(question), "deterministic", "ai_processing_consent_missing_or_revoked"
            plan = validate_plan(_provider("gemini").classify(question))
            return plan, "gemini", None
    except Exception as exc:
        # No exception message/stack is logged; provider content may contain secrets.
        category = "provider_timeout" if "timeout" in type(exc).__name__.lower() else "provider_unavailable_or_invalid_output"
        return deterministic_plan(question), "deterministic", category


def explain(*, workflow_id: str, borrower_id: str, question: str,
            correlation_id: str, configured_mode: str | None = None) -> ExplanationResponse:
    settings = get_settings()
    # Do not classify questions or contact a provider until this exact workflow
    # is positively resolved under the authenticated borrower's ownership.
    with connect() as conn:
        owner = conn.execute(
            "SELECT 1 FROM workflows WHERE id=%s AND borrower_id=%s",
            (workflow_id, borrower_id),
        ).fetchone()
    if not owner:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if len(question) > settings.assistant_max_question_chars:
        raise HTTPException(status_code=422, detail="Question exceeds configured character limit")
    start = time.monotonic()
    try:
        plan, actual_mode, fallback = _classify(question, configured_mode or settings.ai_mode, borrower_id)
        with connect() as conn:
            tool_request = TOOL_INPUTS[plan.tool](workflow_id=workflow_id)
            evidence = TOOL_REGISTRY[plan.tool](conn, tool_request, borrower_id)
        evidence = validate_evidence(evidence, workflow_id)
        answer, flags = render_answer(evidence)
        if fallback == "protected_action_or_untrusted_instruction":
            answer = "I cannot approve, change, or submit an application through the assistant. " + answer
            flags.append("no_protected_action_performed")
        if fallback and actual_mode == "deterministic" and fallback != "protected_action_or_untrusted_instruction":
            flags.append("deterministic_fallback")
        refs = evidence.references
        # Render every citation from server-created records; never accept IDs from model text.
        if refs:
            answer += " Evidence: " + ", ".join(f"{r.kind} {r.identifier}" for r in refs[:6]) + "."
        response = ExplanationResponse(
            answer=answer, tool_identifier=plan.tool, evidence_references=refs, explanation_mode=actual_mode,
            workflow_id=workflow_id, uncertainty_flags=sorted(set(flags + evidence.uncertainty_flags)),
            fallback_reason=fallback, correlation_id=correlation_id,
            simulated_data_disclaimer=DISCLAIMER,
        )
        latency = round((time.monotonic() - start) * 1000, 3)
        append_security_event("assistant_execution", correlation_id, actor_role="borrower",
                              resource_type="workflow", resource_id=workflow_id,
                              details={"mode": actual_mode, "tool": plan.tool.value,
                                       "evidence_references": [{"kind": r.kind, "identifier": r.identifier} for r in refs],
                                       "evidence_kinds": sorted({r.kind for r in refs}),
                                       "validation": "accepted_server_rendered",
                                       "fallback_reason": fallback, "latency_ms": latency,
                                       "error_category": None})
        return response
    except HTTPException:
        raise
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            raise
        # Fail closed on evidence/schema faults. Do not expose SQL or provider details.
        append_security_event("assistant_execution", correlation_id, actor_role="borrower",
                              resource_type="workflow", resource_id=workflow_id,
                              details={"mode": "deterministic", "tool": None,
                                       "evidence_kinds": [], "validation": "rejected",
                                       "fallback_reason": "evidence_unavailable", "latency_ms": round((time.monotonic()-start)*1000,3),
                                       "error_category": "evidence_validation_failure"})
        raise HTTPException(status_code=503, detail="Verified explanation evidence is temporarily unavailable") from exc


def consent_status(borrower_id: str) -> dict:
    with connect() as conn:
        wording = conn.execute(
            "SELECT id,version,consent_text FROM consent_text_versions WHERE purpose=%s ORDER BY created_at DESC LIMIT 1",
            (AI_PURPOSE,),
        ).fetchone()
        grant = conn.execute(
            "SELECT id,status FROM consent_grants WHERE borrower_id=%s AND purpose=%s ORDER BY created_at DESC LIMIT 1",
            (borrower_id, AI_PURPOSE),
        ).fetchone()
    return {"purpose": AI_PURPOSE, "version": wording["version"], "consent_text": wording["consent_text"],
            "grant_id": str(grant["id"]) if grant else None,
            "status": grant["status"] if grant else "not_granted"}


def grant_ai_consent(borrower_id: str, consent_text: str, actor: str,
                     correlation_id: str) -> dict:
    with transaction() as conn:
        wording = conn.execute(
            "SELECT id,version,consent_text FROM consent_text_versions WHERE purpose=%s ORDER BY created_at DESC LIMIT 1",
            (AI_PURPOSE,),
        ).fetchone()
        if not wording or consent_text != wording["consent_text"]:
            raise HTTPException(status_code=422, detail="Consent text must match the current versioned AI disclosure")
        active = conn.execute(
            "SELECT id FROM consent_grants WHERE borrower_id=%s AND purpose=%s AND status='active'",
            (borrower_id, AI_PURPOSE),
        ).fetchone()
        if active:
            raise HTTPException(status_code=409, detail="AI-processing consent is already active")
        digest, key_version = consent_text_hmac(AI_PURPOSE, wording["version"], consent_text)
        row = conn.execute(
            """INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,consent_text_version_id,
               consent_text_hash_algorithm,consent_text_key_version,status)
               VALUES (%s,%s,%s,%s,'hmac-sha256',%s,'active') RETURNING id,status""",
            (borrower_id, AI_PURPOSE, digest, wording["id"], key_version),
        ).fetchone()
    append_security_event("assistant_execution", correlation_id, actor_role="borrower", actor_ref=actor,
                          resource_type="ai_consent", resource_id=str(row["id"]),
                          details={"action": "ai_consent_granted", "purpose": AI_PURPOSE,
                                   "text_version": wording["version"]})
    return {"purpose": AI_PURPOSE, "version": wording["version"], "consent_text": wording["consent_text"],
            "grant_id": str(row["id"]), "status": row["status"]}


def revoke_ai_consent(grant_id: str, borrower_id: str, actor: str, correlation_id: str) -> dict:
    with transaction() as conn:
        grant = conn.execute("SELECT id,status FROM consent_grants WHERE id=%s AND borrower_id=%s AND purpose=%s FOR UPDATE",
                             (grant_id, borrower_id, AI_PURPOSE)).fetchone()
        if not grant:
            raise HTTPException(status_code=404, detail="AI-processing consent grant not found")
        if grant["status"] == "active":
            conn.execute("UPDATE consent_grants SET status='revoked',revoked_at=now() WHERE id=%s", (grant_id,))
    append_security_event("assistant_execution", correlation_id, actor_role="borrower", actor_ref=actor,
                          resource_type="ai_consent", resource_id=grant_id,
                          details={"action": "ai_consent_revoked", "purpose": AI_PURPOSE})
    return {"grant_id": grant_id, "status": "revoked", "external_requests_after_commit": "blocked"}
