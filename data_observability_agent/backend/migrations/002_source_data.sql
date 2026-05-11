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

-- ── NASA Near-Earth Objects (NeoWs API) ──────────────────────────────────────
-- Composite PK prevents duplicate inserts for the same asteroid on the same
-- close-approach date when the DAG is re-run or backfilled.

CREATE TABLE IF NOT EXISTS nasa_near_earth_objects (
    id                                TEXT        NOT NULL,
    close_approach_date               DATE        NOT NULL,
    neo_reference_id                  TEXT,
    name                              TEXT,
    nasa_jpl_url                      TEXT,
    absolute_magnitude_h              NUMERIC,
    estimated_diameter_km_min         NUMERIC,
    estimated_diameter_km_max         NUMERIC,
    is_potentially_hazardous_asteroid BOOLEAN,
    is_sentry_object                  BOOLEAN,
    close_approach_date_full          TEXT,
    relative_velocity_km_per_s        NUMERIC,
    miss_distance_km                  NUMERIC,
    orbiting_body                     TEXT,
    feed_date                         DATE,
    ingested_at                       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, close_approach_date)
);
CREATE INDEX IF NOT EXISTS neo_close_approach_date_idx ON nasa_near_earth_objects (close_approach_date DESC);
CREATE INDEX IF NOT EXISTS neo_hazardous_idx           ON nasa_near_earth_objects (is_potentially_hazardous_asteroid);

-- ── NASA Astronomy Picture of the Day (APOD API) ─────────────────────────────
-- Composite unique key on (date, url) prevents duplicate inserts on re-runs.

CREATE TABLE IF NOT EXISTS nasa_apod (
    date            DATE        NOT NULL,
    url             TEXT        NOT NULL,
    title           TEXT,
    explanation     TEXT,
    hdurl           TEXT,
    media_type      TEXT,
    copyright       TEXT,
    service_version TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (date, url)
);
CREATE INDEX IF NOT EXISTS apod_date_idx ON nasa_apod (date DESC);
