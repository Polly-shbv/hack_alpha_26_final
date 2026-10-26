CREATE TABLE IF NOT EXISTS analysis_batches(id TEXT PRIMARY KEY,owner TEXT NOT NULL,role TEXT NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,ended_at TEXT,idem TEXT,payload TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS batch_idempotency ON analysis_batches(owner,idem) WHERE idem IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS one_active_batch ON analysis_batches(owner) WHERE status IN ('queued','running');
INSERT OR IGNORE INTO schema_migrations VALUES(3);
