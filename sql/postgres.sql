-- PostgreSQL 14+. All mutations of a deployment request and its durable job
-- share one transaction; claim/ack are fenced by monotonically increasing tokens.
CREATE TABLE IF NOT EXISTS services (
 name text PRIMARY KEY CHECK (name ~ '^[a-zA-Z][a-zA-Z0-9_]{0,62}$'),
 desired_version text NOT NULL,
 generation bigint NOT NULL CHECK (generation >= 1),
 active_version text,
 active_generation bigint,
 active_endpoint text,
 active_pid integer,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS deploy_requests (
 request_key text PRIMARY KEY,
 service text NOT NULL REFERENCES services(name),
 version text NOT NULL,
 generation bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS release_jobs (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 service text NOT NULL REFERENCES services(name),
 version text NOT NULL,
 generation bigint NOT NULL,
 state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','CLAIMED','PROMOTED','FAILED')),
 lease_token bigint NOT NULL DEFAULT 0,
 lease_until timestamptz,
 attempts integer NOT NULL DEFAULT 0,
 claimed_by text,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(service,generation)
);
CREATE INDEX IF NOT EXISTS release_jobs_claim_idx ON release_jobs(state,lease_until,id);
CREATE TABLE IF NOT EXISTS effect_outbox (
 effect_id text PRIMARY KEY,
 service text NOT NULL REFERENCES services(name),
 generation bigint NOT NULL,
 payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','CLAIMED','ACKED','FENCED')),
 lease_token bigint NOT NULL DEFAULT 0,
 lease_until timestamptz,
 attempts integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS effect_outbox_claim_idx ON effect_outbox(state,lease_until,created_at);
CREATE TABLE IF NOT EXISTS audit_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 ts timestamptz NOT NULL DEFAULT now(),
 service text NOT NULL,
 generation bigint NOT NULL,
 event text NOT NULL,
 detail text NOT NULL DEFAULT ''
);
