-- Cybersum schema.
--
-- Reconstructed from the code that reads and writes these tables, because the
-- original project carried no DDL: `logs` from src/db_utils.py::insert_log plus
-- the field guide in docs/cybersum_logs_schema.ipynb, and
-- `daily_security_reports` from the INSERT ... ON CONFLICT in db_service.py.
--
-- The UNIQUE constraint on report_date is load-bearing: the daily pipeline's
-- idempotency is `ON CONFLICT (report_date) DO UPDATE`, which is a runtime error
-- rather than an upsert if the constraint is missing. See docs/contracts.md.

-- ── Bronze: raw ingested telemetry ───────────────────────────────────────────
-- Schema-on-read. Collectors write whatever the provider returned into raw_data;
-- every aggregation reads fields back out with `raw_data ->> 'key'`. There is no
-- shared schema object between the two sides, so adding a metric means agreeing
-- on a JSON key in both the collector and the aggregation.
CREATE TABLE IF NOT EXISTS logs (
    id                BIGSERIAL PRIMARY KEY,
    provider          VARCHAR(50)  NOT NULL,   -- cloudflare / azure / uptimerobot
    service           VARCHAR(100),            -- firewall / ddos / uptime / metrics
    log_type          VARCHAR(100),            -- event / alert / uptime-check
    event_timestamp   TIMESTAMPTZ  NOT NULL,   -- provider's own timestamp
    ingested_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    raw_data          JSONB        NOT NULL,
    raw_data_checksum VARCHAR(64)              -- SHA256, for deduplication
);

CREATE INDEX IF NOT EXISTS idx_logs_provider   ON logs (provider);
CREATE INDEX IF NOT EXISTS idx_logs_event_ts   ON logs (event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_raw_json   ON logs USING GIN (raw_data);

-- Every aggregation filters on (provider, service) and a time bound.
CREATE INDEX IF NOT EXISTS idx_logs_provider_service_ts
    ON logs (provider, service, event_timestamp DESC);

-- The evaluation harness tags each synthetic row with raw_data->>'window_id'
-- and queries one window at a time.
CREATE INDEX IF NOT EXISTS idx_logs_window_id
    ON logs ((raw_data ->> 'window_id'));

-- ── Gold: generated briefings ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_security_reports (
    id                SERIAL PRIMARY KEY,
    report_date       DATE         NOT NULL UNIQUE,   -- the idempotency key
    report_content    TEXT,
    status_code       VARCHAR(50)  DEFAULT 'STABLE',  -- STABLE | STATUS A | STATUS B | STATUS C
    top_5_origins     JSONB        DEFAULT '{}'::jsonb,
    total_tokens      INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    model_version     VARCHAR(100),
    execution_id      VARCHAR(255),                   -- ISO 8601, ties DB row to logs and email
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reports_date   ON daily_security_reports (report_date DESC);
CREATE INDEX IF NOT EXISTS idx_reports_status ON daily_security_reports (status_code);

-- Dashboard-facing projection with presentation names.
CREATE OR REPLACE VIEW vw_latest_security_briefings AS
SELECT
    report_date    AS "Report Date",
    report_content AS "Executive Security Briefing",
    status_code    AS "Status",
    total_tokens   AS "AI Token Usage",
    model_version  AS "AI Model Engine",
    created_at     AS "Generation Time"
FROM daily_security_reports
ORDER BY report_date DESC;
