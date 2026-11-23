CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE borrower_profiles (
  id text PRIMARY KEY,
  input_data jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE demo_users (
  username text PRIMARY KEY,
  password_digest text NOT NULL,
  role text NOT NULL CHECK (role IN ('borrower','operator')),
  borrower_id text REFERENCES borrower_profiles(id),
  CHECK ((role='borrower' AND borrower_id IS NOT NULL) OR (role='operator' AND borrower_id IS NULL))
);
CREATE TABLE consent_grants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  borrower_id text NOT NULL REFERENCES borrower_profiles(id),
  purpose text NOT NULL,
  consent_text_hash text NOT NULL CHECK (length(consent_text_hash)=64),
  status text NOT NULL CHECK (status IN ('active','revoked')),
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz
);
CREATE INDEX consent_grants_active_idx ON consent_grants(borrower_id,purpose,status);
CREATE TABLE workflows (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  borrower_id text NOT NULL REFERENCES borrower_profiles(id),
  consent_grant_id uuid NOT NULL REFERENCES consent_grants(id),
  state text NOT NULL CHECK (state IN ('draft','consented','evaluating','compared','awaiting_approval','submitting','submission_unknown','submitted','ineligible','failed','cancelled','consent_revoked')),
  version integer NOT NULL DEFAULT 0 CHECK(version >= 0),
  policy_version text NOT NULL,
  audit_seq bigint NOT NULL DEFAULT 0,
  audit_hash text NOT NULL DEFAULT repeat('0',64),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workflows_owner_idx ON workflows(borrower_id,id);
CREATE TABLE evaluation_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id uuid NOT NULL REFERENCES workflows(id),
  input_data jsonb NOT NULL,
  input_hash text NOT NULL CHECK(length(input_hash)=64),
  policy_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION deny_immutable_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE='55000'; END $$;
CREATE TRIGGER evaluation_snapshots_immutable BEFORE UPDATE OR DELETE ON evaluation_snapshots
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TABLE lender_results (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id uuid NOT NULL REFERENCES workflows(id),
  snapshot_id uuid NOT NULL REFERENCES evaluation_snapshots(id),
  lender_id text NOT NULL,
  result jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workflow_id,lender_id,snapshot_id)
);
CREATE TABLE simulator_ledger (
  lender_id text NOT NULL,
  idempotency_key text NOT NULL,
  payload_hash text NOT NULL CHECK(length(payload_hash)=64),
  response jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(lender_id,idempotency_key)
);
CREATE TABLE workflow_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id uuid NOT NULL REFERENCES workflows(id),
  kind text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','leased','done','failed')),
  payload jsonb NOT NULL,
  result jsonb,
  idempotency_key text NOT NULL,
  attempts integer NOT NULL DEFAULT 0 CHECK(attempts >= 0),
  lease_token uuid,
  lease_expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workflow_id,kind,idempotency_key)
);
CREATE INDEX workflow_steps_claim_idx ON workflow_steps(status,lease_expires_at,created_at);
CREATE TABLE audit_events (
  workflow_id uuid NOT NULL REFERENCES workflows(id),
  sequence bigint NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  actor text NOT NULL,
  action text NOT NULL,
  from_state text,
  to_state text,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  correlation_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  previous_hash text NOT NULL,
  event_hash text NOT NULL,
  PRIMARY KEY(workflow_id,sequence),
  UNIQUE(id)
);
CREATE FUNCTION append_audit_chain() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE seq bigint; prev text; material text;
BEGIN
  UPDATE workflows SET audit_seq=audit_seq+1 WHERE id=NEW.workflow_id
    RETURNING audit_seq,audit_hash INTO seq,prev;
  IF seq IS NULL THEN RAISE EXCEPTION 'workflow not found for audit event'; END IF;
  NEW.sequence := seq;
  NEW.previous_hash := prev;
  material := concat_ws('|',NEW.workflow_id::text,seq::text,NEW.id::text,NEW.actor,NEW.action,
    coalesce(NEW.from_state,''),coalesce(NEW.to_state,''),NEW.details::text,NEW.correlation_id,
    NEW.created_at::text,prev);
  NEW.event_hash := encode(digest(material,'sha256'),'hex');
  UPDATE workflows SET audit_hash=NEW.event_hash WHERE id=NEW.workflow_id;
  RETURN NEW;
END $$;
CREATE TRIGGER audit_chain_before_insert BEFORE INSERT ON audit_events
FOR EACH ROW EXECUTE FUNCTION append_audit_chain();
CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TABLE lab_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operator_username text NOT NULL,
  seed bigint NOT NULL,
  commit_sha text NOT NULL,
  config_hash text NOT NULL,
  environment jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE lab_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES lab_runs(id),
  case_name text NOT NULL,
  outcome jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO borrower_profiles(id,input_data) VALUES
 ('borrower-001','{"monthly_income_minor":420000,"monthly_debt_minor":80000,"requested_amount_minor":2400000,"term_months":24,"employment_months":36,"region":"synthetic-west"}'),
 ('borrower-002','{"monthly_income_minor":230000,"monthly_debt_minor":120000,"requested_amount_minor":3900000,"term_months":36,"employment_months":8,"region":"synthetic-east"}')
ON CONFLICT(id) DO NOTHING;
INSERT INTO schema_migrations(version) VALUES ('001_initial') ON CONFLICT DO NOTHING;
