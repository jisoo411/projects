# Database Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the full PostgreSQL schema — 6 tables, all indexes (HNSW, GIN, B-tree), pgvector/pgcrypto/pg_trgm extensions, soft-delete constraints, and freshness indexes required before any quality checks run.

**Architecture:** All DDL lives in a single idempotent migration file `001_schema.sql`. Tests connect to a real PostgreSQL instance via `DATABASE_URL`. Run the migration once; tests verify the schema is correct.

**Tech Stack:** PostgreSQL 15+, pgvector ≥ 0.5, asyncpg 0.29

---

## File Map

```
backend/
├── migrations/
│   └── 001_schema.sql    # all extensions + 6 tables + all indexes
└── tests/
    └── test_schema.py    # verifies extension, tables, soft-delete columns
```

---

### Task 1: Write the Failing Schema Tests

**Files:**
- Create: `backend/tests/test_schema.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_schema.py
import asyncio
import asyncpg
from config import settings

async def _q(sql: str):
    conn = await asyncpg.connect(settings.database_url)
    result = await conn.fetchval(sql)
    await conn.close()
    return result

def test_vector_extension_installed():
    r = asyncio.run(_q("SELECT extname FROM pg_extension WHERE extname='vector'"))
    assert r is not None, "pgvector not installed — run migrations/001_schema.sql"

def test_pgcrypto_extension_installed():
    r = asyncio.run(_q("SELECT extname FROM pg_extension WHERE extname='pgcrypto'"))
    assert r is not None, "pgcrypto not installed — run migrations/001_schema.sql"

def test_all_six_tables_exist():
    expected = sorted(['airflow_embeddings', 'quality_embeddings', 'live_metrics',
                       'quality_metrics_history', 'dag_status_cache', 'audit_log'])
    actual = asyncio.run(_q(
        "SELECT array_agg(table_name::text ORDER BY table_name) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name = ANY(ARRAY["
        "'airflow_embeddings','quality_embeddings','live_metrics',"
        "'quality_metrics_history','dag_status_cache','audit_log'])"
    ))
    assert sorted(actual) == expected, f"Missing tables: {set(expected)-set(actual)}"

def test_airflow_embeddings_soft_delete_columns():
    cols = asyncio.run(_q(
        "SELECT array_agg(column_name::text ORDER BY column_name) "
        "FROM information_schema.columns "
        "WHERE table_name='airflow_embeddings' AND column_name IN ('is_deleted','deleted_at')"
    ))
    assert sorted(cols) == ['deleted_at', 'is_deleted']

def test_airflow_embeddings_fts_vector_column():
    col = asyncio.run(_q(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='airflow_embeddings' AND column_name='fts_vector'"
    ))
    assert col == 'fts_vector', "fts_vector generated column missing"

def test_quality_embeddings_soft_delete_columns():
    cols = asyncio.run(_q(
        "SELECT array_agg(column_name::text ORDER BY column_name) "
        "FROM information_schema.columns "
        "WHERE table_name='quality_embeddings' AND column_name IN ('is_deleted','deleted_at')"
    ))
    assert sorted(cols) == ['deleted_at', 'is_deleted']

def test_live_metrics_unique_index():
    idx = asyncio.run(_q(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename='live_metrics' AND indexdef LIKE '%schema_name%table_name%metric_type%'"
    ))
    assert idx is not None, "unique index on live_metrics (schema_name, table_name, metric_type) missing"

def test_freshness_index_on_orders():
    idx = asyncio.run(_q(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename='orders' AND indexdef LIKE '%updated_at%'"
    ))
    assert idx is not None, "freshness index on orders(updated_at) missing — required before quality checks"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_schema.py -v
```

Expected: 8 FAIL — tables do not exist yet.

---

### Task 2: Write and Apply the Migration

**Files:**
- Create: `backend/migrations/001_schema.sql`

- [ ] **Step 1: Write `backend/migrations/001_schema.sql`**

```sql
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
    CONSTRAINT soft_delete_consistency CHECK (
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

-- Partial index for alert feed: fast retrieval of high-severity entries
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
    CONSTRAINT soft_delete_consistency CHECK (
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
    last_run_date     TIMESTAMPTZ,   -- logical_date (Airflow 3)
    last_run_end_date TIMESTAMPTZ,
    observed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── audit_log (90-day retention) ────────────────────────────────────────────
-- All chat queries logged. PII-redacted before insert. 90-day rolling retention.
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
-- REQUIRED before any quality freshness check runs — without these, freshness
-- queries do full-table scans and will hit statement_timeout on large tables.
-- Create the monitored tables first if they do not exist (demo/dev only).
CREATE TABLE IF NOT EXISTS orders (
    order_id    TEXT PRIMARY KEY,
    customer_id TEXT,
    status      TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_updated_at_idx ON orders (updated_at DESC);

CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    email       TEXT,
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
```

- [ ] **Step 2: Apply the migration**

```bash
psql $DATABASE_URL -f backend/migrations/001_schema.sql
```

Expected: all `CREATE TABLE`, `CREATE INDEX`, `CREATE EXTENSION` lines complete without errors.

> **Extension upgrade note:** pgvector HNSW indexes cannot be upgraded in place. On major pgvector upgrades run:
> `DROP INDEX CONCURRENTLY ae_hnsw_idx; CREATE INDEX CONCURRENTLY ... USING hnsw ...;`
> For pg_trgm/pgcrypto: `ALTER EXTENSION ... UPDATE;`

- [ ] **Step 3: Run tests to verify they all pass**

```bash
cd backend && python -m pytest tests/test_schema.py -v
```

Expected: PASS (8 tests)

- [ ] **Step 4: Commit**

```bash
git add backend/migrations/001_schema.sql backend/tests/test_schema.py
git commit -m "feat: full pgvector schema — 6 tables, indexes, soft-delete, freshness indexes"
```
