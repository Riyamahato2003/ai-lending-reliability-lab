-- Checkpoint 4: separate consent and non-sensitive assistant observability.
INSERT INTO consent_text_versions(purpose,version,consent_text) VALUES
 ('ai_explanation_processing','v1',
  'I consent to sending my question text to the configured external AI provider to help classify my question. Workflow evidence and financial records are not sent. I can withdraw this consent at any time; local deterministic explanations remain available.')
ON CONFLICT(purpose,version) DO NOTHING;

CREATE UNIQUE INDEX consent_one_active_per_purpose_idx
  ON consent_grants(borrower_id,purpose)
  WHERE purpose='ai_explanation_processing' AND status='active';

ALTER TABLE security_events DROP CONSTRAINT security_events_event_type_check;
ALTER TABLE security_events ADD CONSTRAINT security_events_event_type_check CHECK (event_type IN (
  'authentication_failure','cross_borrower_access_denial','unauthorized_role_action',
  'unauthorized_operator_action','operator_lab_administration','assistant_execution'));

INSERT INTO schema_migrations(version) VALUES ('008_ai_assistant') ON CONFLICT DO NOTHING;

-- Borrower tools expose only owner-filtered audit evidence; the stream remains append-only.
GRANT SELECT ON audit_events TO lending_runtime;
