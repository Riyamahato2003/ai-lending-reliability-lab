import logging
import threading
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from starlette.responses import JSONResponse
from psycopg.types.json import Jsonb

from lending_lab.auth import Principal, get_principal, require_borrower, require_operator, issue_session
from lending_lab.credentials import verify_demo_password
from lending_lab.config import get_settings
from lending_lab.pilot_auth import authenticate_pilot, revoke_pilot_session
from lending_lab.readiness import check_schema_ready, wait_for_schema
from lending_lab.db import append_audit_event, append_security_event, connect, transaction, run_transaction_with_retry
from lending_lab.approvals import create_approval, consume_approval
from lending_lab.dispatch import list_reconciliation_conflicts, record_operator_resolution
from lending_lab.policies import POLICY_VERSION
from lending_lab.schemas import (EvaluationResponse, Role, SessionRequest, SessionResponse, TransitionRequest,
                                 TransitionResponse, WorkflowCreate, WorkflowResponse, ApprovalCreateRequest,
                                 ApprovalConsumeRequest, ApprovalResponse, OperatorResolutionRequest,
                                 LabRunRequest, LabCompareRequest, LabRunResult,
                                 LabScenarioInfo, LabCompareResult)
from lending_lab.workflows import evaluate_workflow, transition
from lending_lab.consent import revoke_consent
from lending_lab.privacy import consent_text_hmac
from lending_lab.reliability_lab import (compare_runs as compare_lab_runs,
                                         get_run as get_lab_run,
                                         list_scenarios as list_lab_scenarios,
                                         run_experiment)
from lending_lab.ai.schemas import (ExplainRequest, ExplanationResponse, CapabilitiesResponse,
                                    ConsentGrantResponse, AIConsentRequest)
from lending_lab.ai.service import (consent_status as ai_consent_status,
                                    explain as explain_workflow, grant_ai_consent,
                                    revoke_ai_consent)

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("lending_lab")


@asynccontextmanager
async def lifespan(_app):
    get_settings().validate_startup()
    await asyncio.to_thread(wait_for_schema)
    yield


app = FastAPI(title="AI Lending Reliability Lab API", version="0.2.0", lifespan=lifespan)
LAB_REQUEST_MAX_BYTES = 16_384
_lab_run_slot = threading.BoundedSemaphore(1)


class LabRequestSizeLimit:
    """Buffer at most 16 KiB for all state-changing JSON requests."""

    def __init__(self, application, max_bytes: int):
        self.application = application
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        # All JSON mutation endpoints must be bounded, not just lab routes.
        # This protects borrower workflow creation, consent and approval too.
        if scope.get("type") != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.application(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    response = JSONResponse({"detail": "Request exceeds 16 KiB"}, status_code=413)
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass
        messages = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                response = JSONResponse({"detail": "Request exceeds 16 KiB"}, status_code=413)
                await response(scope, receive, send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break
        replay = iter(messages)

        async def replay_receive():
            try:
                return next(replay)
            except StopIteration:
                return {"type": "http.disconnect"}

        await self.application(scope, replay_receive, send)


app.add_middleware(LabRequestSizeLimit, max_bytes=LAB_REQUEST_MAX_BYTES)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith(("/pilot/", "/demo/", "/auth/", "/workflows",
                                   "/operator/", "/assistant/")):
        response.headers["Cache-Control"] = "no-store"
    return response



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


@app.get("/ready")
def ready():
    try:
        check_schema_ready()
    except Exception:
        raise HTTPException(status_code=503, detail="Application schema not ready")
    return {"status": "ready"}


@app.get("/auth/capabilities")
def auth_capabilities():
    """Public capability discovery without exposing credentials or secrets."""
    return {"login_mode": "demo" if get_settings().app_mode == "local_demo" else "pilot"}


@app.post("/pilot/sessions", response_model=SessionResponse)
def create_pilot_login(body: SessionRequest, request: Request):
    if get_settings().app_mode != "production":
        raise HTTPException(status_code=404, detail="Pilot sign-in is unavailable")
    try:
        token, claims = authenticate_pilot(body.username, body.password)
    except HTTPException:
        append_security_event("authentication_failure", request.state.correlation_id,
                              details={"reason": "pilot_login_rejected"})
        raise
    return SessionResponse(
        access_token=token,
        expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc),
        role=Role(claims["role"]), borrower_id=claims["borrower_id"]
    )


@app.post("/pilot/sessions/logout", status_code=204)
def logout_pilot_session(principal: Principal = Depends(get_principal)):
    if get_settings().app_mode != "production":
        raise HTTPException(status_code=404, detail="Pilot logout is unavailable")
    revoke_pilot_session(principal)
    return Response(status_code=204)


@app.post("/demo/sessions", response_model=SessionResponse)
def create_session(body: SessionRequest, request: Request):
    # This endpoint is exclusively for synthetic, local developer identities.
    # Production must not authenticate through seeded demo credentials.
    if get_settings().app_mode != "local_demo":
        raise HTTPException(status_code=404, detail="Demo sign-in is unavailable")
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
    if get_settings().app_mode != "local_demo":
        raise HTTPException(status_code=404, detail="Demo logout is unavailable")
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


@app.get("/assistant/consent", response_model=ConsentGrantResponse)
def get_ai_consent(principal: Principal = Depends(require_borrower)):
    return ConsentGrantResponse(**ai_consent_status(principal.borrower_id))


@app.post("/assistant/consent", response_model=ConsentGrantResponse, status_code=201)
def create_ai_consent(body: AIConsentRequest, request: Request,
                      principal: Principal = Depends(require_borrower)):
    return ConsentGrantResponse(**grant_ai_consent(principal.borrower_id, body.consent_text,
                                                   principal.username, request.state.correlation_id))


@app.post("/assistant/consent/{grant_id}/revoke")
def revoke_ai_consent_direct(grant_id: str, request: Request,
                             principal: Principal = Depends(require_borrower)):
    return revoke_ai_consent(grant_id, principal.borrower_id, principal.username,
                             request.state.correlation_id)


@app.get("/workflows/{workflow_id}/assistant/capabilities", response_model=CapabilitiesResponse)
def workflow_assistant_capabilities(workflow_id: str, request: Request,
                                   principal: Principal = Depends(require_borrower)):
    if _log_cross_borrower_attempt(workflow_id, principal.borrower_id, principal,
                                   request.state.correlation_id, "assistant_capabilities"):
        raise HTTPException(status_code=404, detail="Workflow not found")
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM workflows WHERE id=%s AND borrower_id=%s",
                              (workflow_id, principal.borrower_id)).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="Workflow not found")
    status = ai_consent_status(principal.borrower_id)["status"]
    from lending_lab.config import get_settings
    return CapabilitiesResponse(modes=["deterministic", "mock", "gemini"],
                                selected_mode=get_settings().ai_mode,
                                external_ai_consent_required=True,
                                external_ai_consent_active=status == "active",
                                tool_allowlist=["get_workflow_status", "explain_rule_trace",
                                                "compare_simulated_offers", "explain_consent_or_approval_requirements"],
                                actions_available=[])


@app.post("/workflows/{workflow_id}/assistant/explain", response_model=ExplanationResponse)
def explain_workflow_question(workflow_id: str, body: ExplainRequest, request: Request,
                              principal: Principal = Depends(require_borrower)):
    if _log_cross_borrower_attempt(workflow_id, principal.borrower_id, principal,
                                   request.state.correlation_id, "assistant_explain"):
        raise HTTPException(status_code=404, detail="Workflow not found")
    # Positive ownership/existence gate must precede any optional external model call.
    with connect() as conn:
        owned = conn.execute("SELECT 1 FROM workflows WHERE id=%s AND borrower_id=%s",
                             (workflow_id, principal.borrower_id)).fetchone()
    if not owned:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return explain_workflow(workflow_id=workflow_id, borrower_id=principal.borrower_id,
                            question=body.question, correlation_id=request.state.correlation_id,
                            configured_mode=get_settings().ai_mode)


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
    return {"mode": "isolated-reliability-lab", "fault_injection_available": True,
            "principal": principal.username, "lab_data_isolated": True}


@app.get("/operator/lab/scenarios", response_model=list[LabScenarioInfo])
def operator_lab_scenarios(request: Request, principal: Principal = Depends(require_operator)):
    scenarios = list_lab_scenarios()
    append_security_event("operator_lab_administration", request.state.correlation_id,
                          actor_role=principal.role.value, actor_ref=principal.username,
                          resource_type="lab", details={"action": "scenarios_listed"})
    return scenarios


@app.post("/operator/lab/runs", response_model=LabRunResult, status_code=201)
def operator_start_lab_run(body: LabRunRequest, request: Request,
                           principal: Principal = Depends(require_operator)):
    append_security_event("operator_lab_administration", request.state.correlation_id,
                          actor_role=principal.role.value, actor_ref=principal.username,
                          resource_type="lab", details={"action": "run_started",
                          "implementation": body.implementation.value, "scenario": body.scenario.value,
                          "trials": body.trials})
    if not _lab_run_slot.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A bounded lab run is already executing")
    try:
        return run_experiment(body, principal.username)
    finally:
        _lab_run_slot.release()


@app.get("/operator/lab/runs/{run_id}", response_model=LabRunResult)
def operator_get_lab_run(run_id: str, request: Request,
                         principal: Principal = Depends(require_operator)):
    result = get_lab_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="Lab run not found")
    append_security_event("operator_lab_administration", request.state.correlation_id,
                          actor_role=principal.role.value, actor_ref=principal.username,
                          resource_type="lab_run", resource_id=run_id,
                          details={"action": "run_result_viewed"})
    return result


@app.post("/operator/lab/compare", response_model=LabCompareResult)
def operator_compare_lab_runs(body: LabCompareRequest, request: Request,
                              principal: Principal = Depends(require_operator)):
    result = compare_lab_runs(body.safe_run_id, body.unsafe_run_id)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])
    append_security_event("operator_lab_administration", request.state.correlation_id,
                          actor_role=principal.role.value, actor_ref=principal.username,
                          resource_type="lab", details={"action": "runs_compared",
                          "scenario": result["scenario"], "trials": result["trials"]})
    return result


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



# Checkpoint 5: read-only, borrower-scoped presentation endpoints.
@app.get("/workflows")
def list_borrower_workflows(principal: Principal = Depends(require_borrower)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT w.id,w.state,w.version,w.policy_version,w.consent_grant_id,
                      w.created_at,w.updated_at,c.status AS consent_status
                 FROM workflows w JOIN consent_grants c ON c.id=w.consent_grant_id
                WHERE w.borrower_id=%s ORDER BY w.created_at DESC,w.id DESC LIMIT 50""",
            (principal.borrower_id,),
        ).fetchall()
    return [{**row, "id": str(row["id"]), "consent_grant_id": str(row["consent_grant_id"])}
            for row in rows]


@app.get("/workflows/{workflow_id}/evidence/{view}")
def borrower_workflow_evidence(workflow_id: str, view: str,
                               principal: Principal = Depends(require_borrower)):
    # The caller may choose only an explicit read-only presentation view.
    from lending_lab.ai.schemas import (WorkflowStatusInput, RuleTraceInput,
                                        OfferComparisonInput, ConsentRequirementsInput)
    from lending_lab.ai.tools import (get_workflow_status, explain_rule_trace,
                                      compare_simulated_offers, explain_consent_or_approval_requirements)
    views = {
        "status": (WorkflowStatusInput, get_workflow_status),
        "rules": (RuleTraceInput, explain_rule_trace),
        "offers": (OfferComparisonInput, compare_simulated_offers),
        "requirements": (ConsentRequirementsInput, explain_consent_or_approval_requirements),
    }
    if view not in views:
        raise HTTPException(status_code=404, detail="Evidence view not found")
    schema, reader = views[view]
    with connect() as conn:
        evidence = reader(conn, schema(workflow_id=workflow_id), principal.borrower_id)
    from lending_lab.ai.validation import validate_evidence
    return validate_evidence(evidence, workflow_id).model_dump()


@app.get("/operator/lab/runs")
def list_operator_lab_runs(principal: Principal = Depends(require_operator)):
    from lending_lab.db import connect_lab
    with connect_lab() as conn:
        rows = conn.execute(
            """SELECT id,implementation,scenario,seed,trial_count,run_status,
                      effects_created,confirmed_effects,duplicate_effects,created_at
                 FROM lab_runs ORDER BY created_at DESC,id DESC LIMIT 30"""
        ).fetchall()
    return [{**row, "id": str(row["id"])} for row in rows]


@app.post("/operator/dispatch/process-next")
def operator_process_next_simulated_submission(request: Request,
                                               principal: Principal = Depends(require_operator)):
    """Demo-only bounded manual worker action; no new key or approval is created."""
    from lending_lab.dispatch import run_dispatch_worker_once
    if get_settings().app_mode != "local_demo":
        raise HTTPException(status_code=403, detail="Manual dispatch is available only in local demo mode")
    result = run_dispatch_worker_once(correlation_id=request.state.correlation_id)
    append_security_event("operator_lab_administration", request.state.correlation_id,
                          actor_role=principal.role.value, actor_ref=principal.username,
                          resource_type="dispatch_worker",
                          details={"action": "process_one_approved_step", "result_status": result.get("status")})
    return {k:v for k,v in result.items() if k in {"status","reason","workflow_state","intent_id","step_id"}}


# Serve the packaged single-page frontend after all API routes. In development,
# Vite can run separately with its scoped /api-less proxy configuration.
from pathlib import Path as _FrontendPath
from fastapi.staticfiles import StaticFiles as _StaticFiles
_frontend_dist = _FrontendPath(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", _StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
