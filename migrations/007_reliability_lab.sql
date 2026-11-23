ALTER TABLE lab_runs
  ADD COLUMN implementation text NOT NULL DEFAULT 'safe'
    CHECK (implementation IN ('safe','unsafe')),
  ADD COLUMN scenario text NOT NULL DEFAULT 'REQUEST_LOST_BEFORE_LENDER'
    CHECK (scenario IN ('REQUEST_LOST_BEFORE_LENDER','EFFECT_COMMITTED_RESPONSE_LOST',
                        'DUPLICATE_RESPONSE','CONFLICTING_RESPONSE','RESPONSE_AFTER_DEADLINE')),
  ADD COLUMN trial_count integer NOT NULL DEFAULT 1 CHECK (trial_count BETWEEN 1 AND 100),
  ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  ADD COLUMN effects_created integer NOT NULL DEFAULT 0 CHECK (effects_created >= 0),
  ADD COLUMN confirmed_effects integer NOT NULL DEFAULT 0 CHECK (confirmed_effects >= 0),
  ADD COLUMN workflows_marked_submitted integer NOT NULL DEFAULT 0 CHECK (workflows_marked_submitted >= 0),
  ADD COLUMN duplicate_effects integer NOT NULL DEFAULT 0 CHECK (duplicate_effects >= 0),
  ADD COLUMN unauthorized_effects integer NOT NULL DEFAULT 0 CHECK (unauthorized_effects >= 0),
  ADD COLUMN unverified_confirmations integer NOT NULL DEFAULT 0 CHECK (unverified_confirmations >= 0),
  ADD COLUMN duplicate_response_deliveries integer NOT NULL DEFAULT 0 CHECK (duplicate_response_deliveries >= 0),
  ADD COLUMN late_responses integer NOT NULL DEFAULT 0 CHECK (late_responses >= 0),
  ADD COLUMN recovered_workflows integer NOT NULL DEFAULT 0 CHECK (recovered_workflows >= 0),
  ADD COLUMN unresolved_workflows integer NOT NULL DEFAULT 0 CHECK (unresolved_workflows >= 0),
  ADD COLUMN recovery_steps integer NOT NULL DEFAULT 0 CHECK (recovery_steps >= 0),
  ADD COLUMN recovery_duration_ms double precision NOT NULL DEFAULT 0 CHECK (recovery_duration_ms >= 0),
  ADD COLUMN invariant_violations jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN violation_trials jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN started_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN completed_at timestamptz,
  ADD COLUMN experiment_version text NOT NULL DEFAULT '3c-v1',
  ADD COLUMN deterministic_schedule boolean NOT NULL DEFAULT true,
  ADD COLUMN run_status text NOT NULL DEFAULT 'completed'
    CHECK (run_status IN ('running','completed'));
ALTER TABLE lab_runs ALTER COLUMN run_status SET DEFAULT 'running';

ALTER TABLE lab_events ADD COLUMN trial_index integer;
WITH numbered AS (
  SELECT id,row_number() OVER (PARTITION BY run_id ORDER BY created_at,id)-1 AS ordinal
  FROM lab_events
)
UPDATE lab_events e SET trial_index=n.ordinal FROM numbered n WHERE n.id=e.id;
ALTER TABLE lab_events ALTER COLUMN trial_index SET NOT NULL;
ALTER TABLE lab_events ADD CONSTRAINT lab_events_trial_index_check CHECK (trial_index >= 0);
CREATE UNIQUE INDEX lab_events_run_trial_idx ON lab_events(run_id,trial_index);

-- The safe harness commits each synthetic dispatch intent before it can record a
-- lab effect. The unsafe baseline intentionally has no row in this table.
CREATE TABLE lab_dispatch_intents (
  run_id uuid NOT NULL REFERENCES lab_runs(id),
  trial_index integer NOT NULL CHECK (trial_index >= 0),
  lender_id text NOT NULL,
  idempotency_key text NOT NULL,
  payload_hash text NOT NULL CHECK (length(payload_hash)=64),
  approval_binding_digest text NOT NULL CHECK (length(approval_binding_digest)=64),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(run_id,trial_index),
  UNIQUE(run_id,trial_index,lender_id,idempotency_key)
);

-- These lab-only ledgers have no foreign keys or joins to borrower/workflow tables.
CREATE TABLE lab_simulator_ledger (
  run_id uuid NOT NULL REFERENCES lab_runs(id),
  trial_index integer NOT NULL CHECK (trial_index >= 0),
  lender_id text NOT NULL,
  idempotency_key text NOT NULL,
  payload_hash text NOT NULL CHECK (length(payload_hash)=64),
  response jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(run_id,trial_index,lender_id,idempotency_key),
  FOREIGN KEY(run_id,trial_index) REFERENCES lab_dispatch_intents(run_id,trial_index)
);

-- Event rows preserve every simulated effect. Unsafe trials intentionally may have
-- several effects for one key; the safe trial ledger has the uniqueness constraint.
CREATE TABLE lab_effects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES lab_runs(id),
  trial_index integer NOT NULL CHECK (trial_index >= 0),
  effect_index integer NOT NULL CHECK (effect_index >= 1),
  lender_id text NOT NULL,
  idempotency_key text NOT NULL,
  payload_hash text NOT NULL CHECK (length(payload_hash)=64),
  authorized_at_effect boolean NOT NULL,
  logical_step integer NOT NULL CHECK (logical_step >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id,trial_index,effect_index)
);

CREATE FUNCTION protect_lab_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'lab runs are append-only' USING ERRCODE='55000'; END IF;
  IF OLD.run_status <> 'running' OR NEW.run_status <> 'completed' OR
     NEW.completed_at IS NULL OR
     (NEW.id,NEW.operator_username,NEW.seed,NEW.commit_sha,NEW.config_hash,NEW.environment,
      NEW.implementation,NEW.scenario,NEW.trial_count,NEW.configuration,NEW.started_at,
      NEW.experiment_version,NEW.deterministic_schedule,NEW.created_at)
     IS DISTINCT FROM
     (OLD.id,OLD.operator_username,OLD.seed,OLD.commit_sha,OLD.config_hash,OLD.environment,
      OLD.implementation,OLD.scenario,OLD.trial_count,OLD.configuration,OLD.started_at,
      OLD.experiment_version,OLD.deterministic_schedule,OLD.created_at) THEN
    RAISE EXCEPTION 'lab run configuration is immutable; only completion is allowed' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER lab_runs_immutable BEFORE UPDATE OR DELETE ON lab_runs
FOR EACH ROW EXECUTE FUNCTION protect_lab_run();
CREATE TRIGGER lab_events_immutable BEFORE UPDATE OR DELETE ON lab_events
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER lab_intents_immutable BEFORE UPDATE OR DELETE ON lab_dispatch_intents
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER lab_ledger_immutable BEFORE UPDATE OR DELETE ON lab_simulator_ledger
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER lab_effects_immutable BEFORE UPDATE OR DELETE ON lab_effects
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();

REVOKE UPDATE, DELETE ON lab_runs,lab_events FROM lending_lab_operator;
GRANT SELECT,INSERT ON lab_runs,lab_events,lab_dispatch_intents,lab_simulator_ledger,lab_effects TO lending_lab_operator;
GRANT UPDATE (attempt_count,effects_created,confirmed_effects,duplicate_effects,
  workflows_marked_submitted,
  unauthorized_effects,unverified_confirmations,duplicate_response_deliveries,late_responses,
  recovered_workflows,unresolved_workflows,recovery_steps,recovery_duration_ms,
  invariant_violations,violation_trials,metrics,completed_at,run_status)
  ON lab_runs TO lending_lab_operator;

INSERT INTO schema_migrations(version) VALUES ('007_reliability_lab') ON CONFLICT DO NOTHING;
