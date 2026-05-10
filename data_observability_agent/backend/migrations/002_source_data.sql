-- migrations/002_source_data.sql
-- Run against SOURCE_DATABASE_URL (Neon source_data logical database).
-- Idempotent — safe to re-run.

-- ── Business tables monitored by the quality ingestor ───────────────────────
-- Column names must match quality_ingestor.py _MONITORED_TABLES entries:
--   (table, timestamp_col, nullable_col)

CREATE TABLE IF NOT EXISTS orders (
    order_id    TEXT PRIMARY KEY,
    customer_id TEXT,
    status      TEXT,
    amount      NUMERIC,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_updated_at_idx ON orders (updated_at DESC);

CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,   -- nullable_col = 'id'
    email      TEXT,
    name       TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS users_updated_at_idx ON users (updated_at DESC);

CREATE TABLE IF NOT EXISTS inventory_items (
    id         TEXT PRIMARY KEY,   -- nullable_col = 'id'
    name       TEXT,
    quantity   INTEGER,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS inventory_items_updated_at_idx ON inventory_items (updated_at DESC);

CREATE TABLE IF NOT EXISTS revenue_aggregate (
    id          TEXT PRIMARY KEY,   -- nullable_col = 'id'
    date        DATE,
    revenue_usd NUMERIC,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS revenue_aggregate_updated_at_idx ON revenue_aggregate (updated_at DESC);

-- ── Airflow task logs (source for airflow_ingestor.py) ───────────────────────
-- airflow_ingestor reads: dag_id, dag_run_id, task_id, try_number, log_text, logical_date

CREATE TABLE IF NOT EXISTS airflow_task_logs (
    id           BIGSERIAL PRIMARY KEY,
    dag_id       TEXT NOT NULL,
    dag_run_id   TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    try_number   INTEGER NOT NULL DEFAULT 1,
    log_text     TEXT,
    logical_date TIMESTAMPTZ NOT NULL,
    state        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS atl_logical_date_idx ON airflow_task_logs (logical_date DESC);
CREATE INDEX IF NOT EXISTS atl_dag_task_idx     ON airflow_task_logs (dag_id, task_id, logical_date DESC);
