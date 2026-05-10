-- migrations/003_pipeline_watch_fixes.sql
-- Run against DATABASE_URL (Neon pipeline_watch logical database).
-- Idempotent — safe to re-run.

-- ── airflow_embeddings: relax columns the ingestor does not populate ─────────
-- airflow_ingestor.py only inserts: embedding, text, source_uri, dag_id, is_deleted
ALTER TABLE airflow_embeddings ALTER COLUMN task_id      DROP NOT NULL;
ALTER TABLE airflow_embeddings ALTER COLUMN run_id       DROP NOT NULL;
ALTER TABLE airflow_embeddings ALTER COLUMN content_hash DROP NOT NULL;

-- ON CONFLICT (source_uri) in the ingestor requires a full unique index
CREATE UNIQUE INDEX IF NOT EXISTS ae_source_uri_uidx ON airflow_embeddings (source_uri);

-- ── live_metrics: allow 'critical' status written by quality_ingestor.py ─────
ALTER TABLE live_metrics DROP CONSTRAINT IF EXISTS live_metrics_status_check;
ALTER TABLE live_metrics ADD CONSTRAINT live_metrics_status_check
    CHECK (status IN ('ok', 'warn', 'error', 'critical'));

-- ── quality_metrics_history: same status constraint ───────────────────────────
ALTER TABLE quality_metrics_history DROP CONSTRAINT IF EXISTS quality_metrics_history_status_check;
ALTER TABLE quality_metrics_history ADD CONSTRAINT quality_metrics_history_status_check
    CHECK (status IN ('ok', 'warn', 'error', 'critical'));
