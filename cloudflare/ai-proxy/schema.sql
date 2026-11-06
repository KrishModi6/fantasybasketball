-- Courtside AI proxy: in-flight model calls and rolling usage counters.
CREATE TABLE IF NOT EXISTS model_jobs (id TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS model_jobs_expiry ON model_jobs(expires_at);
CREATE TABLE IF NOT EXISTS model_budget (bucket TEXT PRIMARY KEY, calls INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS model_budget_expiry ON model_budget(expires_at);

-- League admins' trade rulings, carried between desktop apps. key_hash is the SHA-256 of the key only the
-- admin's copy holds. To hand a league to a new admin, delete its row in league_admins.
CREATE TABLE IF NOT EXISTS league_admins (league TEXT PRIMARY KEY, key_hash TEXT NOT NULL, claimed_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS league_rulings (
  league TEXT NOT NULL, trade_key TEXT NOT NULL, body TEXT NOT NULL, updated_at INTEGER NOT NULL,
  PRIMARY KEY (league, trade_key)
);
