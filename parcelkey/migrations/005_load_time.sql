-- The time a feed last CHANGED is the time a load stored a row. A file that arrives and stores nothing (every row rejected or unchanged) has a received_at and no loaded_at,
-- so the freshness monitor cannot be moved by a file that applied nothing. A load has a loaded_at exactly when it stored a row.
ALTER TABLE source_load ADD COLUMN IF NOT EXISTS received_at timestamptz;
ALTER TABLE source_load ALTER COLUMN loaded_at DROP NOT NULL;
ALTER TABLE source_load ALTER COLUMN loaded_at DROP DEFAULT;

-- Loads made before this migration: the time they arrived is the loaded_at they have, and one that stored nothing has no loaded_at.
-- FORCE row level security binds the owner, so it is off for these two statements only (the same transaction puts it back).
ALTER TABLE source_load NO FORCE ROW LEVEL SECURITY;
UPDATE source_load SET received_at = loaded_at WHERE received_at IS NULL;
UPDATE source_load SET loaded_at = NULL WHERE stored = 0;
ALTER TABLE source_load FORCE ROW LEVEL SECURITY;

ALTER TABLE source_load ALTER COLUMN received_at SET NOT NULL;
ALTER TABLE source_load ALTER COLUMN received_at SET DEFAULT now();
ALTER TABLE source_load ADD CONSTRAINT source_load_loaded_when_stored CHECK ((stored > 0) = (loaded_at IS NOT NULL));
