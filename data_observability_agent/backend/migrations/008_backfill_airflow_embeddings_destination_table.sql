-- migrations/008_backfill_airflow_embeddings_destination_table.sql
-- Run against PIPELINE_WATCH database.
-- Backfills destination_table on existing airflow_embeddings rows using the
-- dag_id already stored on each row. Cross-database joins are not possible,
-- so the mapping is derived from dag_id directly.

UPDATE airflow_embeddings
SET destination_table = CASE dag_id
    WHEN 'nasa_neo_ingest'  THEN 'nasa_near_earth_objects'
    WHEN 'nasa_apod_ingest' THEN 'nasa_apod'
END
WHERE destination_table IS NULL
  AND dag_id IN ('nasa_neo_ingest', 'nasa_apod_ingest');
