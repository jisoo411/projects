import asyncio

from fastapi import APIRouter

from ingestion.airflow_ingestor import ingest_airflow

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/backfill-airflow-embeddings")
async def backfill_airflow_embeddings():
    """Re-embed all airflow_task_logs rows (no time filter).

    Runs in the background so the request returns immediately.
    Check Render logs for progress — each row emits an embed + upsert.
    """
    async def _run():
        count = await ingest_airflow(full_backfill=True)
        print(f"[backfill] airflow_embeddings: processed {count} rows", flush=True)

    asyncio.create_task(_run())
    return {"status": "started", "note": "check logs for completion"}
