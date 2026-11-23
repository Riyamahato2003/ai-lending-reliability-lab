-- SELECT FOR UPDATE requires table UPDATE privilege. Keep consent rows non-updatable
-- by the runtime role and expose a narrowly scoped owner-controlled lock function.
CREATE FUNCTION lock_consent_grant(p_id uuid) RETURNS SETOF consent_grants
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
  SELECT * FROM public.consent_grants WHERE id=p_id FOR UPDATE
$$;
REVOKE ALL ON FUNCTION lock_consent_grant(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lock_consent_grant(uuid) TO lending_runtime;
INSERT INTO schema_migrations(version) VALUES ('004_lock_functions') ON CONFLICT DO NOTHING;
