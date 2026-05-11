from datetime import datetime, timezone, timedelta
from rag.embedder import embed
from rag.retriever import get_pool, get_source_pool


async def ingest_airflow() -> None:
    """Embed recent Airflow task log snippets into airflow_embeddings.

    Fetches task instances with log_text populated for runs in the last 2 hours.
    Upserts (dag_id, dag_run_id, task_id, try_number) as the natural key.
    """
    source_pool = await get_source_pool()
    write_pool = await get_pool()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)

    async with source_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT dag_id, dag_run_id, task_id, try_number, log_text, logical_date
            FROM airflow_task_logs
            WHERE logical_date >= $1 AND log_text IS NOT NULL
            ORDER BY logical_date DESC
            """,
            cutoff,
        )

    for row in rows:
        ts = row["logical_date"]
        if hasattr(ts, "strftime"):
            ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            ts_str = str(ts)

        source_uri = (
            f"airflow:dag={row['dag_id']}"
            f"/task={row['task_id']}"
            f"/run={row['dag_run_id']}"
            f"/try={row['try_number']}"
            f"/ts={ts_str}"
        )
        chunk_text = (
            f"DAG: {row['dag_id']}\n"
            f"Task: {row['task_id']}\n"
            f"Run: {row['dag_run_id']} (try {row['try_number']})\n"
            f"Log:\n{row['log_text'][:2000]}"
        )
        embedding = await embed(chunk_text)
        # asyncpg binds parameters before SQL-level casts are applied, so the
        # vector must be passed as a formatted string for ::vector to work.
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        async with write_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO airflow_embeddings
                    (embedding, text, source_uri, dag_id, is_deleted)
                VALUES ($1::vector, $2, $3, $4, false)
                ON CONFLICT (source_uri) DO UPDATE
                    SET embedding=$1::vector, text=$2, is_deleted=false
                """,
                embedding_str, chunk_text, source_uri, row["dag_id"],
            )
