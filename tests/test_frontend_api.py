"""Checkpoint 5 presentation API: real borrower role/ownership and operator isolation."""
import pytest
from fastapi.testclient import TestClient
from lending_lab.api import app

pytestmark=pytest.mark.usefixtures("postgres_database")
CONSENT="I consent to synthetic illustrative lender comparison for this demo."
FINANCIAL={"monthly_income_minor":420000,"monthly_debt_minor":80000,
"requested_amount_minor":2400000,"term_months":24,"employment_months":36,"region":"synthetic-west"}

def login(client, username, password):
    r=client.post("/demo/sessions",json={"username":username,"password":password})
    assert r.status_code==200,r.text
    return {"Authorization":"Bearer "+r.json()["access_token"]}

def create(client, auth):
    r=client.post("/workflows",headers=auth,json={"purpose":"illustrative_offer_comparison","consent_text":CONSENT,"financial_details":FINANCIAL})
    assert r.status_code==200,r.text
    return r.json()

def test_borrower_list_is_owner_scoped_and_evidence_views_are_read_only():
    client=TestClient(app)
    a=login(client,"borrower-001","borrower-demo")
    b=login(client,"borrower-002","borrower-demo")
    own=create(client,a)
    foreign=create(client,b)
    listing=client.get("/workflows",headers=a)
    assert listing.status_code==200
    assert own["id"] in [row["id"] for row in listing.json()]
    assert foreign["id"] not in [row["id"] for row in listing.json()]
    before=client.get(f"/workflows/{own['id']}",headers=a).json()
    for view in ("status","rules","offers","requirements"):
        r=client.get(f"/workflows/{own['id']}/evidence/{view}",headers=a)
        assert r.status_code==200,r.text
        assert r.json()["references"]
        assert client.get(f"/workflows/{foreign['id']}/evidence/{view}",headers=a).status_code==404
    assert client.get(f"/workflows/{own['id']}/evidence/mutate",headers=a).status_code==404
    assert client.get(f"/workflows/{own['id']}",headers=a).json()["version"]==before["version"]

def test_evaluated_offers_and_rule_evidence_match_actual_records():
    client=TestClient(app)
    auth=login(client,"borrower-001","borrower-demo")
    wf=create(client,auth)
    ev=client.post(f"/workflows/{wf['id']}/evaluate",headers=auth)
    assert ev.status_code==200,ev.text
    rules=client.get(f"/workflows/{wf['id']}/evidence/rules",headers=auth).json()
    offers=client.get(f"/workflows/{wf['id']}/evidence/offers",headers=auth).json()
    assert rules["facts"]["policy_version"]==wf["policy_version"]
    assert rules["facts"]["lenders"]
    assert offers["facts"]["illustrative_only"] is True
    assert offers["facts"]["comparison_order"]=="sorted by estimated total repayment, then lender ID"
    assert all(row["illustrative_values"]["is_real_offer"] is False for row in offers["facts"]["offers"])

def test_operator_lab_run_history_is_not_borrower_readable():
    client=TestClient(app)
    borrower=login(client,"borrower-001","borrower-demo")
    operator=login(client,"lab-operator","operator-demo")
    assert client.get("/operator/lab/runs",headers=borrower).status_code==403
    assert client.post("/operator/dispatch/process-next",headers=borrower).status_code==403
    ok=client.get("/operator/lab/runs",headers=operator)
    assert ok.status_code==200 and isinstance(ok.json(),list)
    assert client.get("/workflows",headers=operator).status_code==403

def test_missing_workflow_is_rejected_before_explanation():
    client=TestClient(app)
    auth=login(client,"borrower-001","borrower-demo")
    path="/workflows/00000000-0000-0000-0000-000000000099"
    assert client.get(path+"/evidence/status",headers=auth).status_code==404
    assert client.post(path+"/assistant/explain",headers=auth,json={"question":"Why pending?"}).status_code==404
