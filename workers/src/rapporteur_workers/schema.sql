CREATE SCHEMA IF NOT EXISTS rapporteur_queue;
CREATE TABLE IF NOT EXISTS rapporteur_queue.schema_version (
    version integer PRIMARY KEY
);
CREATE TABLE rapporteur_queue.jobs (
    id uuid PRIMARY KEY,
    dedup_key text NOT NULL UNIQUE,
    stage text NOT NULL,
    resource text NOT NULL CHECK (resource IN ('cpu', 'gpu')),
    mode text NOT NULL CHECK (mode IN ('dev', 'gpu')),
    inputs jsonb NOT NULL,
    config jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN (
        'queued', 'running', 'succeeded', 'failed', 'cancelled', 'retry_wait', 'cancel_requested'
    )),
    priority integer NOT NULL,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts integer NOT NULL CHECK (max_attempts > 0),
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_token uuid,
    lease_until timestamptz,
    heartbeat_at timestamptz,
    output jsonb,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((status IN ('running', 'cancel_requested')) =
           (lease_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX jobs_ready ON rapporteur_queue.jobs (mode, resource, priority DESC, available_at)
    WHERE status IN ('queued', 'retry_wait');
CREATE INDEX jobs_expired ON rapporteur_queue.jobs (lease_until)
    WHERE status IN ('running', 'cancel_requested');
INSERT INTO rapporteur_queue.schema_version VALUES (1);
