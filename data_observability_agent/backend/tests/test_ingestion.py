import pytest
from unittest.mock import AsyncMock, patch, MagicMock

MOCK_METRICS = {
    "table_name": "orders", "row_count": 1000, "null_rate": 0.025,
    "freshness_hours": 0.5, "schema_drift": False, "score": 0.92,
    "source_uri": "dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z",
    "observed_at": "2026-04-22T01:00:00+00:00",
}


@pytest.mark.asyncio
async def test_ingest_quality_writes_live_metrics():
    """Quality ingestor upserts a row into live_metrics for each monitored table."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=conn)

    with patch("ingestion.quality_ingestor.get_pool", new=AsyncMock(return_value=pool)), \
         patch("ingestion.quality_ingestor._compute_quality_metrics_async",
               new=AsyncMock(return_value=MOCK_METRICS)):
        from ingestion.quality_ingestor import ingest_quality
        await ingest_quality()

    # live_metrics upsert + quality_metrics_history insert = 2 execute calls per table
    assert conn.execute.call_count >= 2


@pytest.mark.asyncio
async def test_ingest_quality_appends_history():
    """Quality ingestor also writes a row to quality_metrics_history."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=conn)

    with patch("ingestion.quality_ingestor.get_pool", new=AsyncMock(return_value=pool)), \
         patch("ingestion.quality_ingestor._compute_quality_metrics_async",
               new=AsyncMock(return_value=MOCK_METRICS)):
        from ingestion.quality_ingestor import ingest_quality
        await ingest_quality()

    calls_sql = [str(c) for c in conn.execute.call_args_list]
    assert any("quality_metrics_history" in sql for sql in calls_sql)


@pytest.mark.asyncio
async def test_ingest_airflow_embeds_and_upserts():
    """Airflow ingestor fetches logs, embeds, and upserts into airflow_embeddings."""
    mock_row = {
        "dag_id": "orders_pipeline", "dag_run_id": "run_001",
        "task_id": "extract_orders", "try_number": 1,
        "log_text": "KeyError 'order_id'", "logical_date": "2026-04-22T02:14:00+00:00",
    }
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[mock_row])
    conn.execute = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=conn)

    with patch("ingestion.airflow_ingestor.get_pool", new=AsyncMock(return_value=pool)), \
         patch("ingestion.airflow_ingestor.embed", new=AsyncMock(return_value=[0.1] * 1536)):
        from ingestion.airflow_ingestor import ingest_airflow
        await ingest_airflow()

    assert conn.execute.called


@pytest.mark.asyncio
async def test_ingest_airflow_source_uri_format():
    """Embedded chunks use the correct airflow: source URI format."""
    mock_row = {
        "dag_id": "orders_pipeline", "dag_run_id": "run_001",
        "task_id": "extract_orders", "try_number": 1,
        "log_text": "KeyError 'order_id'", "logical_date": "2026-04-22T02:14:00+00:00",
    }
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[mock_row])
    conn.execute = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=conn)

    captured_uris = []

    async def capture_execute(sql, *args, **kwargs):
        if "airflow_embeddings" in sql:
            # source_uri is the 3rd positional arg (after embedding, text)
            if len(args) >= 3:
                captured_uris.append(args[2])
        return None

    conn.execute = AsyncMock(side_effect=capture_execute)

    with patch("ingestion.airflow_ingestor.get_pool", new=AsyncMock(return_value=pool)), \
         patch("ingestion.airflow_ingestor.embed", new=AsyncMock(return_value=[0.1] * 1536)):
        from ingestion.airflow_ingestor import ingest_airflow
        await ingest_airflow()

    assert any(
        uri.startswith("airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=")
        for uri in captured_uris
    )
