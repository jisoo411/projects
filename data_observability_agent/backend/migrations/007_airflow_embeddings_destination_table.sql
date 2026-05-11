-- migrations/007_airflow_embeddings_destination_table.sql
-- Run against PIPELINE_WATCH database (pipeline_watch logical database).
-- Adds destination_table so embeddings carry the target table name for
-- structured filtering and richer RAG context.

ALTER TABLE airflow_embeddings ADD COLUMN IF NOT EXISTS destination_table TEXT;

CREATE INDEX IF NOT EXISTS ae_destination_table_idx ON airflow_embeddings (destination_table)
    WHERE destination_table IS NOT NULL;
