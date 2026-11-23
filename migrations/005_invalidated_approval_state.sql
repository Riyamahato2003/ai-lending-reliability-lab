ALTER TABLE approvals DROP CONSTRAINT approvals_check;
ALTER TABLE approvals ADD CONSTRAINT approvals_consumption_state_check CHECK (
  (status='consumed' AND consumed_at IS NOT NULL) OR
  (status IN ('pending','expired') AND consumed_at IS NULL) OR status='invalidated'
);
INSERT INTO schema_migrations(version) VALUES ('005_invalidated_approval_state') ON CONFLICT DO NOTHING;
