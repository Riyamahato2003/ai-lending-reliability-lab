-- Pilot identities are separate from seeded local demo users.
-- This migration is intentionally late in sequence so earlier schema and
-- least-privilege roles exist before pilot grants are added.
CREATE TABLE pilot_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username text NOT NULL UNIQUE CHECK (
    username = lower(username) AND length(username) BETWEEN 3 AND 80
  ),
  password_digest text NOT NULL,
  role text NOT NULL CHECK (role IN ('borrower','operator')),
  borrower_id text REFERENCES borrower_profiles(id),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((role = 'borrower' AND borrower_id IS NOT NULL)
    OR (role = 'operator' AND borrower_id IS NULL))
);
CREATE UNIQUE INDEX pilot_accounts_borrower_idx
  ON pilot_accounts(borrower_id) WHERE borrower_id IS NOT NULL;

CREATE TABLE pilot_sessions (
  session_id uuid PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES pilot_accounts(id),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pilot_sessions_active_idx ON pilot_sessions(account_id,expires_at)
  WHERE revoked_at IS NULL;

-- A durable per-account limiter shared by all API instances. External ingress
-- must additionally enforce rate limits on source IP and request volume.
CREATE TABLE pilot_login_limits (
  username_hash text PRIMARY KEY CHECK(length(username_hash)=64),
  attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 1000000),
  window_started timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON pilot_accounts, pilot_sessions, pilot_login_limits FROM PUBLIC;
GRANT SELECT ON pilot_accounts TO lending_runtime;
GRANT SELECT, INSERT ON pilot_sessions TO lending_runtime;
GRANT UPDATE (revoked_at) ON pilot_sessions TO lending_runtime;
GRANT SELECT, INSERT ON pilot_login_limits TO lending_runtime;
GRANT UPDATE (attempts,window_started) ON pilot_login_limits TO lending_runtime;

-- Account creation, disablement and credential reset require migration-owner
-- access and must never be available from an unauthenticated HTTP endpoint.
INSERT INTO schema_migrations(version)
VALUES ('999_pilot_identity') ON CONFLICT DO NOTHING;
