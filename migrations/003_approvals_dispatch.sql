ALTER TABLE workflows DROP CONSTRAINT workflows_state_check;
ALTER TABLE workflows ADD CONSTRAINT workflows_state_check CHECK (state IN (
  'draft','consented','evaluating','compared','awaiting_approval','submitting','dispatching',
  'submission_unknown','submitted','ineligible','failed','cancelled','consent_revoked'
));

ALTER TABLE lender_results ADD COLUMN offer_expires_at timestamptz;

CREATE TABLE approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  borrower_id text NOT NULL REFERENCES borrower_profiles(id),
  workflow_id uuid NOT NULL REFERENCES workflows(id),
  snapshot_id uuid NOT NULL REFERENCES evaluation_snapshots(id),
  snapshot_hash text NOT NULL CHECK (length(snapshot_hash)=64),
  canonical_payload text NOT NULL,
  payload_hmac text NOT NULL CHECK (length(payload_hmac)=64),
  hmac_key_version text NOT NULL,
  consent_grant_id uuid NOT NULL REFERENCES consent_grants(id),
  consent_purpose text NOT NULL,
  policy_version text NOT NULL,
  workflow_version integer NOT NULL CHECK (workflow_version >= 0),
  lender_id text NOT NULL,
  lender_version text NOT NULL,
  offer_version text NOT NULL,
  offer_amount_minor bigint NOT NULL CHECK (offer_amount_minor > 0),
  term_months integer NOT NULL CHECK (term_months > 0),
  offer_values jsonb NOT NULL,
  offer_expires_at timestamptz NOT NULL,
  approval_expires_at timestamptz NOT NULL,
  idempotency_key text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','consumed','invalidated','expired')),
  consumed_at timestamptz,
  invalidation_reason text,
  created_at timestamptz NOT NULL,
  created_by text NOT NULL,
  CHECK ((status IN ('consumed','invalidated') AND consumed_at IS NOT NULL) OR
         (status IN ('pending','expired') AND consumed_at IS NULL)),
  UNIQUE(workflow_id,id),
  UNIQUE(lender_id,idempotency_key)
);
CREATE INDEX approvals_pending_workflow_idx ON approvals(workflow_id,status,approval_expires_at);
ALTER TABLE workflow_steps ADD COLUMN approval_id uuid REFERENCES approvals(id);

CREATE FUNCTION protect_approval_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'approvals are retained and cannot be deleted' USING ERRCODE='55000'; END IF;
  IF (NEW.id,NEW.borrower_id,NEW.workflow_id,NEW.snapshot_id,NEW.snapshot_hash,NEW.canonical_payload,
      NEW.payload_hmac,NEW.hmac_key_version,NEW.consent_grant_id,NEW.consent_purpose,NEW.policy_version,
      NEW.workflow_version,NEW.lender_id,NEW.lender_version,NEW.offer_version,NEW.offer_amount_minor,
      NEW.term_months,NEW.offer_values,NEW.offer_expires_at,NEW.approval_expires_at,NEW.idempotency_key,
      NEW.created_at,NEW.created_by)
     IS DISTINCT FROM
     (OLD.id,OLD.borrower_id,OLD.workflow_id,OLD.snapshot_id,OLD.snapshot_hash,OLD.canonical_payload,
      OLD.payload_hmac,OLD.hmac_key_version,OLD.consent_grant_id,OLD.consent_purpose,OLD.policy_version,
      OLD.workflow_version,OLD.lender_id,OLD.lender_version,OLD.offer_version,OLD.offer_amount_minor,
      OLD.term_months,OLD.offer_values,OLD.offer_expires_at,OLD.approval_expires_at,OLD.idempotency_key,
      OLD.created_at,OLD.created_by) THEN
    RAISE EXCEPTION 'approval binding is immutable' USING ERRCODE='55000';
  END IF;
  IF NOT ((OLD.status='pending' AND NEW.status IN ('consumed','invalidated','expired')) OR
          (OLD.status='consumed' AND NEW.status='invalidated')) THEN
    RAISE EXCEPTION 'approval status transition is invalid' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER approvals_protect_binding BEFORE UPDATE OR DELETE ON approvals
FOR EACH ROW EXECUTE FUNCTION protect_approval_binding();

CREATE TABLE submission_intents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id uuid NOT NULL UNIQUE REFERENCES workflows(id),
  approval_id uuid NOT NULL UNIQUE REFERENCES approvals(id),
  snapshot_id uuid NOT NULL REFERENCES evaluation_snapshots(id),
  lender_id text NOT NULL,
  idempotency_key text NOT NULL,
  lender_payload jsonb NOT NULL,
  canonical_payload text NOT NULL,
  payload_hash text NOT NULL CHECK (length(payload_hash)=64),
  approval_payload_hmac text NOT NULL CHECK (length(approval_payload_hmac)=64),
  created_at timestamptz NOT NULL,
  correlation_id text NOT NULL,
  UNIQUE(lender_id,idempotency_key)
);
CREATE TRIGGER submission_intents_immutable BEFORE UPDATE OR DELETE ON submission_intents
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();

CREATE TABLE operator_resolution_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  intent_id uuid NOT NULL REFERENCES submission_intents(id),
  operator_username text NOT NULL,
  disposition text NOT NULL CHECK (disposition IN ('remain_unresolved','evidence_recorded')),
  evidence text NOT NULL CHECK (length(evidence) BETWEEN 1 AND 2000),
  created_at timestamptz NOT NULL
);
CREATE INDEX operator_resolution_intent_idx ON operator_resolution_events(intent_id,created_at);
CREATE TRIGGER operator_resolution_events_immutable BEFORE UPDATE OR DELETE ON operator_resolution_events
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();

GRANT SELECT,INSERT ON approvals TO lending_runtime;
GRANT UPDATE (status,consumed_at,invalidation_reason) ON approvals TO lending_runtime;
GRANT SELECT,INSERT ON submission_intents,operator_resolution_events TO lending_runtime;
GRANT SELECT,INSERT ON workflow_steps TO lending_runtime;
GRANT UPDATE (approval_id) ON workflow_steps TO lending_runtime;

INSERT INTO schema_migrations(version) VALUES ('003_approvals_dispatch') ON CONFLICT DO NOTHING;
