from fastapi import APIRouter

from ingestion.airflow_ingestor import ingest_airflow

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/backfill-airflow-embeddings")
async def backfill_airflow_embeddings():
    """Re-embed all airflow_task_logs rows (no time filter).

    Runs synchronously — the request blocks until complete and returns the
    row count. Any DB or embedding errors surface as HTTP 500.
    """
    count = await ingest_airflow(full_backfill=True)
    return {"status": "done", "rows_processed": count}
