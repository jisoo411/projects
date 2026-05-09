from tools.db_quality_tool import _compute_quality_metrics_async
from rag.retriever import get_pool

_MONITORED_TABLES = [
    ("orders",            "updated_at", "order_id"),
    ("users",             "updated_at", "id"),
    ("inventory_items",   "updated_at", "id"),
    ("revenue_aggregate", "updated_at", "id"),
]


async def ingest_quality() -> None:
    """Compute quality metrics for all monitored tables.

    Upserts the latest snapshot into live_metrics (one row per table) and
    appends a time-series row to quality_metrics_history.
    """
    pool = await get_pool()
    for table_name, ts_col, nullable_col in _MONITORED_TABLES:
        metrics = await _compute_quality_metrics_async(table_name, ts_col, nullable_col)
        status = (
            "ok" if metrics["score"] >= 0.8
            else "warn" if metrics["score"] >= 0.6
            else "critical"
        )
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO live_metrics
                    (schema_name, table_name, metric_type, value, status, observed_at, source)
                VALUES ('public', $1, 'composite', $2, $3, NOW(), $4)
                ON CONFLICT (schema_name, table_name, metric_type)
                DO UPDATE SET value=$2, status=$3, observed_at=NOW(), source=$4
                """,
                table_name, metrics["score"], status, metrics["source_uri"],
            )
            await conn.execute(
                """
                INSERT INTO quality_metrics_history
                    (table_name, metric_type, value, status, observed_at, source)
                VALUES ($1, 'composite', $2, $3, NOW(), $4)
                """,
                table_name, metrics["score"], status, metrics["source_uri"],
            )
