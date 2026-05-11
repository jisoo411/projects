-- migrations/004_quality_embeddings_fix.sql
-- Run against DATABASE_URL (Neon pipeline_watch logical database).
-- Idempotent — safe to re-run.

-- Allow 'composite' metric_type so quality_ingestor.py can embed composite scores.
ALTER TABLE quality_embeddings DROP CONSTRAINT IF EXISTS quality_embeddings_metric_type_check;
ALTER TABLE quality_embeddings ADD CONSTRAINT quality_embeddings_metric_type_check
    CHECK (metric_type IN ('null_rate', 'row_count', 'freshness', 'schema_drift', 'composite'));

-- ON CONFLICT (source_uri) requires a unique index.
CREATE UNIQUE INDEX IF NOT EXISTS qe_source_uri_uidx ON quality_embeddings (source_uri);

-- content_hash is not populated by the ingestor — make it optional.
ALTER TABLE quality_embeddings ALTER COLUMN content_hash DROP NOT NULL;
