CREATE TABLE consent_text_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  purpose text NOT NULL,
  version text NOT NULL,
  consent_text text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(purpose,version)
);
CREATE TRIGGER consent_text_versions_immutable BEFORE UPDATE OR DELETE ON consent_text_versions
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
INSERT INTO consent_text_versions(purpose,version,consent_text) VALUES
 ('illustrative_offer_comparison','v1','I consent to synthetic illustrative lender comparison for this demo.')
ON CONFLICT(purpose,version) DO NOTHING;

ALTER TABLE consent_grants ADD COLUMN consent_text_version_id uuid REFERENCES consent_text_versions(id);
ALTER TABLE consent_grants ADD COLUMN consent_text_hash_algorithm text NOT NULL DEFAULT 'hmac-sha256';
ALTER TABLE consent_grants ADD COLUMN consent_text_key_version text NOT NULL DEFAULT 'legacy-v1';

CREATE TABLE sensitive_borrower_data (
  borrower_id text PRIMARY KEY REFERENCES borrower_profiles(id),
  input_data jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO sensitive_borrower_data(borrower_id,input_data)
SELECT id,input_data FROM borrower_profiles ON CONFLICT(borrower_id) DO NOTHING;
ALTER TABLE borrower_profiles DROP COLUMN input_data;

CREATE TABLE sensitive_evaluation_snapshot_data (
  snapshot_id uuid PRIMARY KEY REFERENCES evaluation_snapshots(id) ON DELETE CASCADE,
  input_data jsonb NOT NULL
);
INSERT INTO sensitive_evaluation_snapshot_data(snapshot_id,input_data)
SELECT id,input_data FROM evaluation_snapshots;
ALTER TABLE evaluation_snapshots DROP COLUMN input_data;

CREATE TABLE sensitive_lender_result_data (
  lender_result_id uuid PRIMARY KEY REFERENCES lender_results(id) ON DELETE CASCADE,
  result jsonb NOT NULL
);
INSERT INTO sensitive_lender_result_data(lender_result_id,result)
SELECT id,result FROM lender_results;
ALTER TABLE lender_results DROP COLUMN result;

CREATE TABLE sensitive_dispatch_payloads (
  intent_id uuid PRIMARY KEY REFERENCES submission_intents(id) ON DELETE CASCADE,
  lender_payload jsonb NOT NULL,
  canonical_payload text NOT NULL
);
INSERT INTO sensitive_dispatch_payloads(intent_id,lender_payload,canonical_payload)
SELECT id,lender_payload,canonical_payload FROM submission_intents;
ALTER TABLE submission_intents DROP COLUMN lender_payload;
ALTER TABLE submission_intents DROP COLUMN canonical_payload;

CREATE TABLE sensitive_approval_data (
  approval_id uuid PRIMARY KEY REFERENCES approvals(id) ON DELETE CASCADE,
  canonical_payload text NOT NULL,
  offer_amount_minor bigint NOT NULL,
  term_months integer NOT NULL,
  offer_values jsonb NOT NULL
);
INSERT INTO sensitive_approval_data(approval_id,canonical_payload,offer_amount_minor,term_months,offer_values)
SELECT id,canonical_payload,offer_amount_minor,term_months,offer_values FROM approvals;
ALTER TABLE approvals ALTER COLUMN canonical_payload DROP NOT NULL;
ALTER TABLE approvals ALTER COLUMN offer_amount_minor DROP NOT NULL;
ALTER TABLE approvals ALTER COLUMN term_months DROP NOT NULL;
ALTER TABLE approvals ALTER COLUMN offer_values DROP NOT NULL;

CREATE OR REPLACE FUNCTION protect_approval_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE scrub_allowed boolean;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'approvals are retained and cannot be deleted' USING ERRCODE='55000'; END IF;
  scrub_allowed := NEW.status='invalidated'
    AND (OLD.canonical_payload IS NULL OR NEW.canonical_payload IS NULL)
    AND (OLD.offer_amount_minor IS NULL OR NEW.offer_amount_minor IS NULL)
    AND (OLD.term_months IS NULL OR NEW.term_months IS NULL)
    AND (OLD.offer_values IS NULL OR NEW.offer_values IS NULL);
  IF (NEW.id,NEW.borrower_id,NEW.workflow_id,NEW.snapshot_id,NEW.snapshot_hash,NEW.payload_hmac,
      NEW.hmac_key_version,NEW.consent_grant_id,NEW.consent_purpose,NEW.policy_version,
      NEW.workflow_version,NEW.lender_id,NEW.lender_version,NEW.offer_version,NEW.offer_expires_at,
      NEW.approval_expires_at,NEW.idempotency_key,NEW.created_at,NEW.created_by)
     IS DISTINCT FROM
     (OLD.id,OLD.borrower_id,OLD.workflow_id,OLD.snapshot_id,OLD.snapshot_hash,OLD.payload_hmac,
      OLD.hmac_key_version,OLD.consent_grant_id,OLD.consent_purpose,OLD.policy_version,
      OLD.workflow_version,OLD.lender_id,OLD.lender_version,OLD.offer_version,OLD.offer_expires_at,
      OLD.approval_expires_at,OLD.idempotency_key,OLD.created_at,OLD.created_by) THEN
    RAISE EXCEPTION 'approval binding is immutable' USING ERRCODE='55000';
  END IF;
  IF NOT scrub_allowed AND
     (NEW.canonical_payload,NEW.offer_amount_minor,NEW.term_months,NEW.offer_values)
       IS DISTINCT FROM (OLD.canonical_payload,OLD.offer_amount_minor,OLD.term_months,OLD.offer_values) THEN
    RAISE EXCEPTION 'approval sensitive fields may only be erased' USING ERRCODE='55000';
  END IF;
  IF NOT ((OLD.status='pending' AND NEW.status IN ('consumed','invalidated','expired')) OR
          (OLD.status IN ('consumed','expired') AND NEW.status='invalidated') OR OLD.status=NEW.status) THEN
    RAISE EXCEPTION 'approval status transition is invalid' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION protect_consent_grant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'consent grants are retained' USING ERRCODE='55000'; END IF;
  IF (NEW.id,NEW.borrower_id,NEW.purpose,NEW.consent_text_hash,NEW.consent_text_version_id,
      NEW.consent_text_hash_algorithm,NEW.consent_text_key_version,NEW.created_at)
     IS DISTINCT FROM
     (OLD.id,OLD.borrower_id,OLD.purpose,OLD.consent_text_hash,OLD.consent_text_version_id,
      OLD.consent_text_hash_algorithm,OLD.consent_text_key_version,OLD.created_at) THEN
    RAISE EXCEPTION 'consent grant binding is immutable' USING ERRCODE='55000';
  END IF;
  IF NOT (OLD.status='active' AND NEW.status='revoked' AND NEW.revoked_at IS NOT NULL)
     AND (NEW.status,NEW.revoked_at) IS DISTINCT FROM (OLD.status,OLD.revoked_at) THEN
    RAISE EXCEPTION 'consent may only be revoked once' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER consent_grants_protect BEFORE UPDATE OR DELETE ON consent_grants
FOR EACH ROW EXECUTE FUNCTION protect_consent_grant();

GRANT SELECT ON consent_text_versions TO lending_runtime;
GRANT SELECT,INSERT,DELETE ON sensitive_borrower_data,sensitive_evaluation_snapshot_data,
  sensitive_lender_result_data,sensitive_dispatch_payloads,sensitive_approval_data TO lending_runtime;
GRANT UPDATE (input_data,updated_at) ON sensitive_borrower_data TO lending_runtime;
GRANT UPDATE (status,revoked_at) ON consent_grants TO lending_runtime;
GRANT UPDATE (canonical_payload,offer_amount_minor,term_months,offer_values) ON approvals TO lending_runtime;

INSERT INTO schema_migrations(version) VALUES ('006_consent_privacy') ON CONFLICT DO NOTHING;
