from datetime import datetime, timezone, timedelta
from rag.embedder import embed
from rag.retriever import get_pool, get_source_pool


async def ingest_airflow(full_backfill: bool = False) -> int:
    """Embed Airflow task log snippets into airflow_embeddings.

    full_backfill=False (default): only rows from the last 2 hours.
    full_backfill=True: all rows — used by the one-time backfill endpoint.
    Returns the number of rows processed.
    """
    source_pool = await get_source_pool()
    write_pool = await get_pool()

    async with source_pool.acquire() as conn:
        if full_backfill:
            rows = await conn.fetch(
                """
                SELECT dag_id, dag_run_id, task_id, try_number, log_text, logical_date,
                       destination_table
                FROM airflow_task_logs
                WHERE log_text IS NOT NULL
                ORDER BY logical_date DESC
                """
            )
        else:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
            rows = await conn.fetch(
                """
                SELECT dag_id, dag_run_id, task_id, try_number, log_text, logical_date,
                       destination_table
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

        destination_table = row["destination_table"]
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
            + (f"Destination table: {destination_table}\n" if destination_table else "")
            + f"Log:\n{row['log_text'][:2000]}"
        )
        embedding = await embed(chunk_text)
        # asyncpg binds parameters before SQL-level casts are applied, so the
        # vector must be passed as a formatted string for ::vector to work.
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        async with write_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO airflow_embeddings
                    (embedding, text, source_uri, dag_id, destination_table, is_deleted)
                VALUES ($1::vector, $2, $3, $4, $5, false)
                ON CONFLICT (source_uri) DO UPDATE
                    SET embedding=$1::vector, text=$2, destination_table=$5, is_deleted=false
                """,
                embedding_str, chunk_text, source_uri, row["dag_id"], destination_table,
            )
    return len(rows)
