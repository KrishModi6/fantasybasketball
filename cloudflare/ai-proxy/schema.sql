-- Courtside AI proxy: in-flight model calls and rolling usage counters.
CREATE TABLE IF NOT EXISTS model_jobs (id TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS model_jobs_expiry ON model_jobs(expires_at);
CREATE TABLE IF NOT EXISTS model_budget (bucket TEXT PRIMARY KEY, calls INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS model_budget_expiry ON model_budget(expires_at);
