-- migrations/005_drop_dag_status_cache.sql
-- Run against DATABASE_URL (Neon pipeline_watch logical database).
-- Removes the dag_status_cache table — nothing writes to it; dag status is
-- derived from airflow_task_logs in source_data instead.

DROP TABLE IF EXISTS dag_status_cache;
