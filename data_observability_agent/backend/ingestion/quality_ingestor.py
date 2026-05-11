from rag.embedder import embed
from rag.retriever import get_pool, get_source_pool
from tools.db_quality_tool import _compute_quality_metrics_async

# Preferred column names for timestamp and null-rate checks, in priority order.
_TS_CANDIDATES = ["updated_at", "ingested_at", "created_at", "logical_date"]


async def _discover_tables() -> list[tuple[str, str, str]]:
    """Return (table_name, timestamp_col, nullable_col) for every user table in source_data."""
    pool = await get_source_pool()
    async with pool.acquire() as conn:
        table_names = [
            r["table_name"] for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            )
        ]

        result = []
        for table in table_names:
            col_names = {
                r["column_name"] for r in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = $1",
                    table,
                )
            }

            ts_col = next((c for c in _TS_CANDIDATES if c in col_names), None)
            if ts_col is None:
                continue  # no recognisable timestamp column — skip

            pk_row = await conn.fetchrow(
                "SELECT kcu.column_name FROM information_schema.key_column_usage kcu "
                "JOIN information_schema.table_constraints tc "
                "ON kcu.constraint_name = tc.constraint_name "
                "AND kcu.table_schema = tc.table_schema "
                "WHERE tc.table_schema = 'public' AND tc.table_name = $1 "
                "AND tc.constraint_type = 'PRIMARY KEY' "
                "ORDER BY kcu.ordinal_position LIMIT 1",
                table,
            )
            nullable_col = pk_row["column_name"] if pk_row else ts_col

            result.append((table, ts_col, nullable_col))

    return result


async def ingest_quality() -> None:
    """Compute quality metrics for all tables in source_data.

    Upserts snapshots into live_metrics and quality_metrics_history,
    then embeds a text summary into quality_embeddings for RAG retrieval.
    """
    tables = await _discover_tables()
    pool = await get_pool()

    for table_name, ts_col, nullable_col in tables:
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
