import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from psycopg.types.json import Jsonb

from lending_lab.auth import Principal, get_principal, require_borrower, require_operator, issue_session
from lending_lab.credentials import verify_demo_password
from lending_lab.config import get_settings
from lending_lab.db import append_audit_event, append_security_event, connect, transaction, run_transaction_with_retry
from lending_lab.approvals import create_approval, consume_approval
from lending_lab.dispatch import list_reconciliation_conflicts, record_operator_resolution
from lending_lab.policies import POLICY_VERSION
from lending_lab.schemas import (EvaluationResponse, Role, SessionRequest, SessionResponse, TransitionRequest,
                                 TransitionResponse, WorkflowCreate, WorkflowResponse, ApprovalCreateRequest,
                                 ApprovalConsumeRequest, ApprovalResponse, OperatorResolutionRequest)
from lending_lab.workflows import evaluate_workflow, transition
from lending_lab.consent import revoke_consent
from lending_lab.privacy import consent_text_hmac

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("lending_lab")


@asynccontextmanager
async def lifespan(_app):
    get_settings().validate_startup()
    yield


app = FastAPI(title="AI Lending Reliability Lab API", version="0.2.0", lifespan=lifespan)


def _log_cross_borrower_attempt(workflow_id: str, borrower_id: str, actor: Principal,
                                correlation_id: str, route_name: str) -> bool:
    with connect() as conn:
        owner = conn.execute("SELECT borrower_id FROM workflows WHERE id=%s", (workflow_id,)).fetchone()
    if owner and owner["borrower_id"] != borrower_id:
        append_security_event("cross_borrower_access_denial", correlation_id,
                              actor_role=actor.role.value, actor_ref=actor.username,
                              resource_type="workflow", resource_id=workflow_id,
                              details={"route": route_name})
        return True
    return False


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["x-correlation-id"] = correlation_id
    logger.info('{"event":"http_request","method":"%s","path":"%s","status":%s,"correlation_id":"%s"}',
                request.method, request.url.path, response.status_code, correlation_id)
    return response


@app.get("/health")
def health():
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "database": "connected", "mode": "synthetic-demo"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.post("/demo/sessions", response_model=SessionResponse)
def create_session(body: SessionRequest, request: Request):
    with connect() as conn:
        user = conn.execute("SELECT username,password_digest,role,borrower_id FROM demo_users WHERE username=%s", (body.username,)).fetchone()
    if not user or not verify_demo_password(body.username, body.password, user["password_digest"]):
        append_security_event("authentication_failure", request.state.correlation_id,
                              details={"reason": "invalid_demo_credentials"})
        raise HTTPException(status_code=401, detail="Invalid demo credentials")
    token, claims = issue_session(user["username"], Role(user["role"]), user["borrower_id"])
    with transaction() as conn:
        conn.execute("INSERT INTO demo_sessions(session_id,username,role,borrower_id,expires_at) VALUES (%s,%s,%s,%s,to_timestamp(%s))",
                     (claims["jti"], user["username"], user["role"], user["borrower_id"], claims["exp"]))
    return SessionResponse(access_token=token, expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc),
                           role=user["role"], borrower_id=user["borrower_id"])


@app.post("/demo/sessions/logout", status_code=204)
def logout_session(principal: Principal = Depends(get_principal)):
    with transaction() as conn:
        conn.execute("UPDATE demo_sessions SET revoked_at=now() WHERE session_id=%s AND username=%s AND revoked_at IS NULL",
                     (principal.session_id, principal.username))
    return Response(status_code=204)


@app.post("/workflows", response_model=WorkflowResponse)
def create_workflow(body: WorkflowCreate, request: Request, principal: Principal = Depends(require_borrower)):
    with transaction() as conn:
        wording = conn.execute(
            "SELECT id,version,consent_text FROM consent_text_versions WHERE purpose=%s ORDER BY created_at DESC LIMIT 1",
            (body.purpose,),
        ).fetchone()
        if not wording or body.consent_text != wording["consent_text"]:
            raise HTTPException(status_code=422, detail="Consent text must match the current versioned purpose text")
        if body.financial_details:
            conn.execute(
                """INSERT INTO sensitive_borrower_data(borrower_id,input_data) VALUES (%s,%s)
                   ON CONFLICT(borrower_id) DO UPDATE SET input_data=excluded.input_data,updated_at=now()""",
                (principal.borrower_id, Jsonb(body.financial_details.model_dump())),
            )
        elif not conn.execute("SELECT 1 FROM sensitive_borrower_data WHERE borrower_id=%s",
                              (principal.borrower_id,)).fetchone():
            raise HTTPException(status_code=409, detail="Re-enter synthetic financial details to start a new consented workflow")
        consent_hash, consent_key_version = consent_text_hmac(body.purpose, wording["version"], body.consent_text)
        consent = conn.execute(
            """INSERT INTO consent_grants(borrower_id,purpose,consent_text_hash,consent_text_version_id,
               consent_text_hash_algorithm,consent_text_key_version,status)
               VALUES (%s,%s,%s,%s,'hmac-sha256',%s,'active') RETURNING id""",
            (principal.borrower_id, body.purpose, consent_hash, wording["id"], consent_key_version)).fetchone()
        row = conn.execute("INSERT INTO workflows(borrower_id,consent_grant_id,state,policy_version) VALUES (%s,%s,'consented',%s) RETURNING *",
                           (principal.borrower_id, consent["id"], POLICY_VERSION)).fetchone()
        append_audit_event(conn, row["id"], principal.username, "consent.granted", None, "consented",
                           {"purpose": body.purpose,
                            "consent_text_version": wording["version"],
                            "consent_text_key_version": consent_key_version},
                           request.state.correlation_id)
    return WorkflowResponse(id=str(row["id"]), borrower_id=row["borrower_id"], state=row["state"],
                            version=row["version"], policy_version=row["policy_version"], consent_grant_id=str(consent["id"]))


@app.get("/consent-text/{purpose}")
def get_consent_text(purpose: str, principal: Principal = Depends(require_borrower)):
    with connect() as conn:
        row = conn.execute("SELECT version,consent_text FROM consent_text_versions WHERE purpose=%s ORDER BY created_at DESC LIMIT 1",
                           (purpose,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Consent text not found")
    return {"purpose": purpose, "version": row["version"], "consent_text": row["consent_text"]}


@app.post("/consents/{grant_id}/revoke")
def revoke_borrower_consent(grant_id: str, request: Request,
                            principal: Principal = Depends(require_borrower)):
    with connect() as conn:
        owner = conn.execute("SELECT borrower_id FROM consent_grants WHERE id=%s", (grant_id,)).fetchone()
    if owner and owner["borrower_id"] != principal.borrower_id:
        append_security_event("cross_borrower_access_denial", request.state.correlation_id,
                              actor_role=principal.role.value, actor_ref=principal.username,
                              resource_type="consent_grant", resource_id=grant_id,
                              details={"route": "revoke_consent"})
        raise HTTPException(status_code=404, detail="Consent grant not found")
    return run_transaction_with_retry(lambda conn: revoke_consent(
        conn, grant_id, principal.borrower_id, principal.username, request.state.correlation_id))


@app.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str, request: Request, principal: Principal = Depends(require_borrower)):
    with connect() as conn:
        row = conn.execute("SELECT id,borrower_id,state,version,policy_version,created_at,updated_at FROM workflows WHERE id=%s AND borrower_id=%s",
                           (workflow_id, principal.borrower_id)).fetchone()
    if not row:
        _log_cross_borrower_attempt(workflow_id, principal.borrower_id, principal,
                                    request.state.correlation_id, "get_workflow")
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {**row, "id": str(row["id"])}


@app.post("/workflows/{workflow_id}/evaluate", response_model=EvaluationResponse)
def run_evaluation(workflow_id: str, request: Request, principal: Principal = Depends(require_borrower)):
    if _log_cross_borrower_attempt(workflow_id, principal.borrower_id, principal,
                                   request.state.correlation_id, "evaluate_workflow"):
        raise HTTPException(status_code=404, detail="Workflow not found")
    with transaction() as conn:
        result = evaluate_workflow(conn, workflow_id, principal.borrower_id, request.state.correlation_id)
    return result


@app.post("/workflows/{workflow_id}/transition", response_model=TransitionResponse)
def transition_workflow(workflow_id: str, body: TransitionRequest, request: Request,
                        principal: Principal = Depends(require_borrower)):
    if body.target.value != "cancelled":
        append_security_event("unauthorized_role_action", request.state.correlation_id,
                              actor_role=principal.role.value, actor_ref=principal.username,
                              resource_type="workflow", resource_id=workflow_id,
                              details={"reason": "protected_transition_not_available"})
        raise HTTPException(status_code=403, detail="Only cancellation is borrower-triggerable in this checkpoint")
    if _log_cross_borrower_attempt(workflow_id, principal.borrower_id, principal,
                                   request.state.correlation_id, "transition_workflow"):
        raise HTTPException(status_code=404, detail="Workflow not found")
    with transaction() as conn:
        result = transition(conn, workflow_id, principal.borrower_id, body.target,
                            body.expected_version, principal.username, request.state.correlation_id)
    return result


@app.post("/workflows/{workflow_id}/approvals", response_model=ApprovalResponse)
def create_workflow_approval(workflow_id: str, body: ApprovalCreateRequest, request: Request,
                             principal: Principal = Depends(require_borrower)):
    if _log_cross_borrower_attempt(workflow_id, principal.borrower_id, principal,
                                   request.state.correlation_id, "create_approval"):
        raise HTTPException(status_code=404, detail="Workflow not found")
    result = run_transaction_with_retry(lambda conn: create_approval(
        conn, workflow_id, principal.borrower_id, body.lender_id, body.expected_version,
        body.approval_ttl_seconds, principal.username, request.state.correlation_id))
    return ApprovalResponse(**result)


@app.post("/workflows/{workflow_id}/approvals/{approval_id}/consume")
def consume_workflow_approval(workflow_id: str, approval_id: str, body: ApprovalConsumeRequest,
                              request: Request, principal: Principal = Depends(require_borrower)):
    if _log_cross_borrower_attempt(workflow_id, principal.borrower_id, principal,
                                   request.state.correlation_id, "consume_approval"):
        raise HTTPException(status_code=404, detail="Workflow not found")
    result = run_transaction_with_retry(lambda conn: consume_approval(
        conn, workflow_id, principal.borrower_id, approval_id, body.expected_workflow_version,
        principal.username, request.state.correlation_id))
    if result.get("error"):
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@app.get("/operator/lab/status")
def operator_scaffold(request: Request, principal: Principal = Depends(require_operator)):
    append_security_event("operator_lab_administration", request.state.correlation_id,
                          actor_role=principal.role.value, actor_ref=principal.username,
                          resource_type="lab", details={"action": "status_view"})
    return {"mode": "operator-scaffold", "fault_injection_available": False,
            "principal": principal.username, "lab_data_isolated": True}


@app.get("/operator/reconciliation-conflicts")
def operator_reconciliation_conflicts(request: Request, principal: Principal = Depends(require_operator)):
    with connect() as conn:
        conflicts = list_reconciliation_conflicts(conn)
    append_security_event("operator_lab_administration", request.state.correlation_id,
                          actor_role=principal.role.value, actor_ref=principal.username,
                          resource_type="submission_intents", details={"action": "conflicts_view", "count": len(conflicts)})
    return {"conflicts": conflicts}


@app.post("/operator/reconciliation-conflicts/{intent_id}/reviews")
def operator_record_reconciliation_review(intent_id: str, body: OperatorResolutionRequest,
                                          request: Request,
                                          principal: Principal = Depends(require_operator)):
    result = run_transaction_with_retry(lambda conn: record_operator_resolution(
        conn, intent_id, principal.username, body.disposition, body.evidence,
        request.state.correlation_id))
    append_security_event("operator_lab_administration", request.state.correlation_id,
                          actor_role=principal.role.value, actor_ref=principal.username,
                          resource_type="submission_intent", resource_id=intent_id,
                          details={"action": "conflict_review_recorded", "disposition": body.disposition})
    return result
