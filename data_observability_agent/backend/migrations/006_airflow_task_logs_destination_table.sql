-- migrations/006_airflow_task_logs_destination_table.sql
-- Run against SOURCE_DATABASE_URL (Neon source_data logical database).
-- Adds destination_table so each DAG self-registers its target table on first run.

ALTER TABLE airflow_task_logs ADD COLUMN IF NOT EXISTS destination_table TEXT;

CREATE INDEX IF NOT EXISTS atl_destination_table_idx ON airflow_task_logs (destination_table);
