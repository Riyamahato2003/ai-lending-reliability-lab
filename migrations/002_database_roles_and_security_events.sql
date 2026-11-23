CREATE TABLE demo_sessions (
  session_id uuid PRIMARY KEY,
  username text NOT NULL REFERENCES demo_users(username),
  role text NOT NULL CHECK (role IN ('borrower','operator')),
  borrower_id text REFERENCES borrower_profiles(id),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((role='borrower' AND borrower_id IS NOT NULL) OR (role='operator' AND borrower_id IS NULL))
);
CREATE INDEX demo_sessions_active_idx ON demo_sessions(session_id,expires_at) WHERE revoked_at IS NULL;

ALTER TABLE workflow_steps ADD COLUMN max_attempts integer NOT NULL DEFAULT 5 CHECK(max_attempts BETWEEN 1 AND 20);
ALTER TABLE workflow_steps ADD COLUMN available_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE workflow_steps ADD COLUMN dead_lettered_at timestamptz;
ALTER TABLE workflow_steps DROP CONSTRAINT workflow_steps_status_check;
ALTER TABLE workflow_steps ADD CONSTRAINT workflow_steps_status_check
  CHECK(status IN ('queued','leased','done','failed','dead_letter'));

CREATE TABLE security_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type text NOT NULL CHECK (event_type IN (
    'authentication_failure','cross_borrower_access_denial','unauthorized_role_action',
    'unauthorized_operator_action','operator_lab_administration')),
  actor_role text CHECK (actor_role IS NULL OR actor_role IN ('borrower','operator')),
  actor_ref text,
  resource_type text,
  resource_id text,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  correlation_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX security_events_created_idx ON security_events(created_at DESC);
CREATE TRIGGER security_events_immutable BEFORE UPDATE OR DELETE ON security_events
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER simulator_ledger_immutable BEFORE UPDATE OR DELETE ON simulator_ledger
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();

CREATE OR REPLACE FUNCTION append_audit_chain() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE seq bigint; prev text; material text;
BEGIN
  UPDATE public.workflows SET audit_seq=audit_seq+1 WHERE id=NEW.workflow_id
    RETURNING audit_seq,audit_hash INTO seq,prev;
  IF seq IS NULL THEN RAISE EXCEPTION 'workflow not found for audit event'; END IF;
  NEW.sequence := seq;
  NEW.previous_hash := prev;
  material := concat_ws('|',NEW.workflow_id::text,seq::text,NEW.id::text,NEW.actor,NEW.action,
    coalesce(NEW.from_state,''),coalesce(NEW.to_state,''),NEW.details::text,NEW.correlation_id,
    NEW.created_at::text,prev);
  NEW.event_hash := encode(public.digest(material,'sha256'),'hex');
  UPDATE public.workflows SET audit_hash=NEW.event_hash WHERE id=NEW.workflow_id;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION append_audit_event(
  p_workflow_id uuid, p_actor text, p_action text, p_from_state text, p_to_state text,
  p_details jsonb, p_correlation_id text
) RETURNS TABLE(event_sequence bigint, event_hash text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
  RETURN QUERY INSERT INTO public.audit_events AS inserted(workflow_id,actor,action,from_state,to_state,details,correlation_id)
    VALUES (p_workflow_id,p_actor,p_action,p_from_state,p_to_state,coalesce(p_details,'{}'::jsonb),p_correlation_id)
    RETURNING inserted.sequence,inserted.event_hash;
END $$;

CREATE OR REPLACE FUNCTION verify_audit_chain(p_workflow_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
  SELECT coalesce(bool_and(sequence = expected_sequence
    AND previous_hash = expected_previous_hash
    AND event_hash = recomputed_hash), true)
  FROM (
    SELECT sequence,
      row_number() OVER (ORDER BY sequence) AS expected_sequence,
      coalesce(lag(event_hash) OVER (ORDER BY sequence), repeat('0',64)) AS expected_previous_hash,
      previous_hash,event_hash,
      encode(public.digest(concat_ws('|',workflow_id::text,sequence::text,id::text,actor,action,
        coalesce(from_state,''),coalesce(to_state,''),details::text,correlation_id,created_at::text,previous_hash),
        'sha256'),'hex') AS recomputed_hash
    FROM public.audit_events WHERE workflow_id=p_workflow_id
  ) AS ordered_events
$$;

CREATE OR REPLACE FUNCTION append_security_event(
  p_event_type text, p_actor_role text, p_actor_ref text, p_resource_type text,
  p_resource_id text, p_details jsonb, p_correlation_id text
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE result_id uuid;
BEGIN
  INSERT INTO public.security_events(event_type,actor_role,actor_ref,resource_type,resource_id,details,correlation_id)
  VALUES (p_event_type,p_actor_role,p_actor_ref,p_resource_type,p_resource_id,coalesce(p_details,'{}'::jsonb),p_correlation_id)
  RETURNING id INTO result_id;
  RETURN result_id;
END $$;

-- Runtime and lab roles are created by the PostgreSQL bootstrap, never by the API.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lending_runtime, lending_lab_operator;

GRANT SELECT ON borrower_profiles, demo_users TO lending_runtime;
GRANT SELECT, INSERT ON demo_sessions TO lending_runtime;
GRANT UPDATE (revoked_at) ON demo_sessions TO lending_runtime;
GRANT SELECT, INSERT ON consent_grants TO lending_runtime;
GRANT SELECT ON workflows TO lending_runtime;
GRANT INSERT ON workflows TO lending_runtime;
GRANT UPDATE (state,version,updated_at) ON workflows TO lending_runtime;
GRANT SELECT, INSERT ON evaluation_snapshots, lender_results TO lending_runtime;
GRANT SELECT, INSERT ON simulator_ledger TO lending_runtime;
GRANT SELECT, INSERT ON workflow_steps TO lending_runtime;
GRANT UPDATE (status,result,attempts,lease_token,lease_expires_at,updated_at,available_at,dead_lettered_at) ON workflow_steps TO lending_runtime;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO lending_runtime;
GRANT EXECUTE ON FUNCTION append_audit_event(uuid,text,text,text,text,jsonb,text) TO lending_runtime;
GRANT EXECUTE ON FUNCTION append_security_event(text,text,text,text,text,jsonb,text) TO lending_runtime;

GRANT SELECT, INSERT, UPDATE, DELETE ON lab_runs, lab_events TO lending_lab_operator;
GRANT EXECUTE ON FUNCTION append_security_event(text,text,text,text,text,jsonb,text) TO lending_lab_operator;

INSERT INTO schema_migrations(version) VALUES ('002_database_roles_and_security_events') ON CONFLICT DO NOTHING;
