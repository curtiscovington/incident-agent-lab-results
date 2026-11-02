CREATE TABLE IF NOT EXISTS jobs (
 id uuid PRIMARY KEY, idempotency_key text NOT NULL UNIQUE, payload text NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','completed','failed')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), completed_at timestamptz,
 owner text, lease_token uuid, lease_until timestamptz, attempts int NOT NULL DEFAULT 0,
 result text, max_attempts int NOT NULL DEFAULT 5
);
CREATE TABLE IF NOT EXISTS outbox (id bigserial PRIMARY KEY, job_id uuid NOT NULL REFERENCES jobs(id), published boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS completions (job_id uuid PRIMARY KEY REFERENCES jobs(id), worker text NOT NULL, result text NOT NULL, finished_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS heartbeats (name text PRIMARY KEY, role text NOT NULL, leader boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(id) WHERE NOT published;
CREATE INDEX IF NOT EXISTS active_leases ON jobs(lease_until) WHERE state='running';
