-- Row level security. Every table with an agency_id is FORCED: a connection sees and writes a row only when agency_id equals the
-- app.agency_id setting of its current transaction. No setting means no rows. FORCE binds the table owner too.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['agency', 'source_load', 'source_record', 'ingest_reject', 'property_link', 'quarantine',
                             'contact', 'contact_identifier', 'contact_link', 'contact_merge', 'contact_quarantine'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS agency_isolation ON %I', t);
        EXECUTE format('CREATE POLICY agency_isolation ON %I USING (agency_id = current_setting(''app.agency_id'', true)) WITH CHECK (agency_id = current_setting(''app.agency_id'', true))', t);
    END LOOP;
END $$;
