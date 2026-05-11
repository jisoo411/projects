from tools.db_quality_tool import _compute_quality_metrics_async
from rag.embedder import embed
from rag.retriever import get_pool

_MONITORED_TABLES = [
    ("orders",            "updated_at", "order_id"),
    ("users",             "updated_at", "id"),
    ("inventory_items",   "updated_at", "id"),
    ("revenue_aggregate", "updated_at", "id"),
]


async def ingest_quality() -> None:
    """Compute quality metrics for all monitored tables.

    Upserts the latest snapshot into live_metrics and quality_metrics_history,
    then embeds a text summary into quality_embeddings for RAG retrieval.
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

        chunk_text = (
            f"Table: {table_name}\n"
            f"Quality score: {metrics['score']} (status: {status})\n"
            f"Null rate: {metrics['null_rate']}\n"
            f"Row count: {metrics['row_count']}\n"
            f"Freshness: {metrics['freshness_hours']} hours since last update\n"
            f"Schema drift detected: {metrics['schema_drift']}\n"
            f"Observed at: {metrics['observed_at']}"
        )
        embedding = await embed(chunk_text)
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO quality_embeddings
                    (embedding, text, source_uri, table_name, schema_name,
                     metric_type, metric_value, is_deleted, observed_at)
                VALUES ($1::vector, $2, $3, $4, 'public', 'composite', $5, false, NOW())
                ON CONFLICT (source_uri) DO UPDATE
                    SET embedding=$1::vector, text=$2, metric_value=$5,
                        is_deleted=false, observed_at=NOW()
                """,
                embedding_str, chunk_text, metrics["source_uri"],
                table_name, metrics["score"],
            )
