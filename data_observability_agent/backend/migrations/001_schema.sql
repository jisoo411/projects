-- migrations/001_schema.sql
-- Idempotent — safe to re-run.

-- ── Extensions ─────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector: VECTOR type + HNSW index
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- kept for future; FTS is primary keyword path

-- ── airflow_embeddings ──────────────────────────────────────────────────────
-- Stores sanitised task-log sections from Airflow. One row per unique chunk.
-- 'simple' FTS config: preserves error codes, snake_case, stack symbols (no stemming).
CREATE TABLE IF NOT EXISTS airflow_embeddings (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text         TEXT NOT NULL,
    embedding    VECTOR(1536) NOT NULL,
    dag_id       TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    try_number   INTEGER NOT NULL DEFAULT 1,
    severity     TEXT CHECK (severity IN ('INFO','WARNING','ERROR','CRITICAL')),
    error_sig    TEXT,
    content_hash TEXT NOT NULL,
    source_uri   TEXT NOT NULL,   -- airflow:dag=.../task=.../run=.../try=.../ts=...
    object_key   TEXT,            -- S3/GCS object key for presigned URL retrieval
    deleted_at   TIMESTAMPTZ,
    is_deleted   BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata     JSONB,
    fts_vector   TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    CONSTRAINT ae_soft_delete_consistency CHECK (
        (is_deleted = false AND deleted_at IS NULL) OR
        (is_deleted = true  AND deleted_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS ae_hnsw_idx
    ON airflow_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS ae_dag_created_idx
    ON airflow_embeddings (dag_id, created_at DESC) WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS ae_error_sig_idx
    ON airflow_embeddings (error_sig);

CREATE INDEX IF NOT EXISTS ae_severity_idx
    ON airflow_embeddings (severity);

CREATE UNIQUE INDEX IF NOT EXISTS ae_content_hash_uidx
    ON airflow_embeddings (content_hash);

CREATE INDEX IF NOT EXISTS ae_fts_gin_idx
    ON airflow_embeddings USING gin (fts_vector) WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS ae_severity_created_partial_idx
    ON airflow_embeddings (severity, created_at DESC)
    WHERE severity IN ('ERROR', 'CRITICAL') AND is_deleted = false;

-- ── quality_embeddings ──────────────────────────────────────────────────────
-- Stores quality check summaries + runbook docs. 'english' FTS: prose benefits from stemming.
CREATE TABLE IF NOT EXISTS quality_embeddings (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text         TEXT NOT NULL,
    embedding    VECTOR(1536) NOT NULL,
    table_name   TEXT NOT NULL,
    schema_name  TEXT NOT NULL DEFAULT 'public',
    metric_type  TEXT NOT NULL CHECK (metric_type IN ('null_rate','row_count','freshness','schema_drift')),
    metric_value NUMERIC,
    rule_id      TEXT,
    content_hash TEXT NOT NULL,
    source_uri   TEXT NOT NULL,   -- dbcheck:table=.../metric=.../ts=...
    object_key   TEXT,
    deleted_at   TIMESTAMPTZ,
    is_deleted   BOOLEAN NOT NULL DEFAULT false,
    observed_at  TIMESTAMPTZ NOT NULL,
    metadata     JSONB,
    fts_vector   TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    CONSTRAINT qe_soft_delete_consistency CHECK (
        (is_deleted = false AND deleted_at IS NULL) OR
        (is_deleted = true  AND deleted_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS qe_hnsw_idx
    ON quality_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS qe_table_observed_idx
    ON quality_embeddings (table_name, observed_at DESC) WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS qe_rule_id_idx
    ON quality_embeddings (rule_id);

CREATE UNIQUE INDEX IF NOT EXISTS qe_content_hash_uidx
    ON quality_embeddings (content_hash);

CREATE INDEX IF NOT EXISTS qe_fts_gin_idx
    ON quality_embeddings USING gin (fts_vector) WHERE is_deleted = false;

-- ── live_metrics (dashboard cache) ─────────────────────────────────────────
-- Latest-snapshot cache: one row per (schema, table, metric). Upserted every 15 min.
CREATE TABLE IF NOT EXISTS live_metrics (
    id           SERIAL PRIMARY KEY,
    schema_name  TEXT NOT NULL DEFAULT 'public',
    table_name   TEXT NOT NULL,
    metric_type  TEXT NOT NULL,
    value        NUMERIC,
    status       TEXT NOT NULL CHECK (status IN ('ok','warn','error')),
    observed_at  TIMESTAMPTZ NOT NULL,
    source       TEXT NOT NULL   -- dbcheck:table=.../metric=.../ts=...
);

CREATE UNIQUE INDEX IF NOT EXISTS lm_schema_table_metric_uidx
    ON live_metrics (schema_name, table_name, metric_type);

-- ── quality_metrics_history (90-day time series) ───────────────────────────
-- Full time series retained for trend sparklines and explainability.
CREATE TABLE IF NOT EXISTS quality_metrics_history (
    id           BIGSERIAL PRIMARY KEY,
    schema_name  TEXT NOT NULL DEFAULT 'public',
    table_name   TEXT NOT NULL,
    metric_type  TEXT NOT NULL,
    value        NUMERIC,
    status       TEXT NOT NULL CHECK (status IN ('ok','warn','error')),
    observed_at  TIMESTAMPTZ NOT NULL,
    source       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS qmh_lookup_idx
    ON quality_metrics_history (schema_name, table_name, metric_type, observed_at DESC);

-- ── dag_status_cache (Airflow fallback) ────────────────────────────────────
-- Populated during every ingestion cycle. Read by Workflow Sub-Agent when Airflow MCP is down.
CREATE TABLE IF NOT EXISTS dag_status_cache (
    dag_id            TEXT PRIMARY KEY,
    is_paused         BOOLEAN,
    schedule          TEXT,
    last_run_id       TEXT,
    last_run_state    TEXT,
    last_run_date     TIMESTAMPTZ,
    last_run_end_date TIMESTAMPTZ,
    observed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── audit_log (90-day retention) ────────────────────────────────────────────
-- All chat queries logged. PII-redacted before insert.
CREATE TABLE IF NOT EXISTS audit_log (
    id                BIGSERIAL PRIMARY KEY,
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    api_key_id        TEXT NOT NULL,   -- hmac(AUDIT_HMAC_SECRET, raw_key, sha256)[:16]
    query_text        TEXT NOT NULL,   -- PII-redacted before insert
    guardrail_outcome TEXT NOT NULL,   -- allowed | blocked_topic | blocked_injection | blocked_pii
    agent_status      TEXT,            -- ok | partial | degraded
    response_ms       INTEGER
);

CREATE INDEX IF NOT EXISTS al_occurred_at_idx ON audit_log (occurred_at DESC);

-- ── Freshness indexes on monitored tables ──────────────────────────────────
-- Create monitored tables if they do not exist (dev/demo scaffold).
CREATE TABLE IF NOT EXISTS orders (
    order_id    TEXT PRIMARY KEY,
    customer_id TEXT,
    status      TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_updated_at_idx ON orders (updated_at DESC);

CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    email        TEXT,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS users_last_seen_at_idx ON users (last_seen_at DESC);

CREATE TABLE IF NOT EXISTS inventory_items (
    item_id    TEXT PRIMARY KEY,
    quantity   INTEGER,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS inventory_items_updated_at_idx ON inventory_items (updated_at DESC);

CREATE TABLE IF NOT EXISTS revenue_aggregate (
    date        DATE PRIMARY KEY,
    revenue_usd NUMERIC,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS revenue_aggregate_computed_at_idx ON revenue_aggregate (computed_at DESC);
