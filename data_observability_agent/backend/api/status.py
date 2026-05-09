from fastapi import APIRouter
from tools.db_quality_tool import get_cached_metrics
from rag.retriever import get_pool

router = APIRouter()

_MONITORED_TABLES = ["orders", "users", "inventory_items", "revenue_aggregate"]
_MONITORED_DAGS = ["orders_pipeline", "user_sync_dag", "inventory_load", "revenue_agg"]


async def _get_dag_status_cache() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT dag_id, state, observed_at FROM dag_status_cache "
            "WHERE dag_id = ANY($1::text[]) ORDER BY observed_at DESC",
            _MONITORED_DAGS,
        )
    return [dict(r) for r in rows]


@router.get("/status")
async def status():
    table_rows = []
    for tbl in _MONITORED_TABLES:
        cached = await get_cached_metrics(tbl)
        if cached:
            table_rows.append(cached)
        else:
            table_rows.append({"table_name": tbl, "status": "no_data"})

    dag_rows = await _get_dag_status_cache()

    return {"tables": table_rows, "dags": dag_rows}
