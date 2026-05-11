from fastapi import APIRouter
from rag.retriever import get_pool, get_source_pool

router = APIRouter()

_MONITORED_DAGS = ["nasa_neo_ingest", "nasa_apod_ingest"]


async def _get_dag_status_cache() -> list[dict]:
    pool = await get_source_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (dag_id)
                dag_id, state, logical_date AS observed_at
            FROM airflow_task_logs
            WHERE dag_id = ANY($1::text[])
            ORDER BY dag_id, logical_date DESC
            """,
            _MONITORED_DAGS,
        )
    return [dict(r) for r in rows]


@router.get("/status")
async def status():
    pool = await get_pool()
    async with pool.acquire() as conn:
        table_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (table_name)
                table_name, metric_type, value, status, observed_at, source
            FROM live_metrics
            WHERE schema_name = 'public'
            ORDER BY table_name, observed_at DESC
            """
        )

    dag_rows = await _get_dag_status_cache()

    return {"tables": [dict(r) for r in table_rows], "dags": dag_rows}
