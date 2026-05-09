# Ingestion Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the background ingestion pipeline — an Airflow ingestor that embeds recent DAG run logs into `airflow_embeddings` and a quality ingestor that computes quality metrics and writes them to both `live_metrics` (upsert) and `quality_metrics_history` (append). APScheduler runs both on a 15-minute cron in the FastAPI process.

**Architecture:** `ingest_airflow()` fetches failed/running DAG run logs from the last 2 hours via asyncpg, embeds them via `embed()`, upserts into `airflow_embeddings`. `ingest_quality()` calls `_compute_quality_metrics_async()` for each monitored table, upserts `live_metrics`, and appends `quality_metrics_history`. APScheduler `AsyncIOScheduler` starts in the lifespan of `main.py`.

**Tech Stack:** asyncpg (two pools — `get_pool` for pipeline_watch writes, `get_source_pool` for source-table reads), APScheduler 3.10, `rag.embedder.embed()`

---

## File Map

```
backend/
├── ingestion/
│   ├── __init__.py      # empty package marker
│   ├── airflow_ingestor.py
│   └── quality_ingestor.py
└── tests/
    └── test_ingestion.py
```

---

### Task 1: Write Failing Tests

**Files:**
- Create: `backend/tests/test_ingestion.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_ingestion.py
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

    with patch("ingestion.airflow_ingestor.get_source_pool", new=AsyncMock(return_value=pool)), \
         patch("ingestion.airflow_ingestor.get_pool", new=AsyncMock(return_value=pool)), \
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

    with patch("ingestion.airflow_ingestor.get_source_pool", new=AsyncMock(return_value=pool)), \
         patch("ingestion.airflow_ingestor.get_pool", new=AsyncMock(return_value=pool)), \
         patch("ingestion.airflow_ingestor.embed", new=AsyncMock(return_value=[0.1] * 1536)):
        from ingestion.airflow_ingestor import ingest_airflow
        await ingest_airflow()

    assert any(
        uri.startswith("airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=")
        for uri in captured_uris
    )
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_ingestion.py -v
```

Expected: FAIL — `ingestion.quality_ingestor` and `ingestion.airflow_ingestor` not found.

---

### Task 2: Implement the Ingestors

**Files:**
- Create: `backend/ingestion/__init__.py`
- Create: `backend/ingestion/quality_ingestor.py`
- Create: `backend/ingestion/airflow_ingestor.py`

- [ ] **Step 1: Write `backend/ingestion/__init__.py`**

```python
```

(Empty — package marker only.)

- [ ] **Step 2: Write `backend/ingestion/quality_ingestor.py`**

```python
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
```

- [ ] **Step 3: Write `backend/ingestion/airflow_ingestor.py`**

```python
from datetime import datetime, timezone, timedelta
from rag.embedder import embed
from rag.retriever import get_pool

async def ingest_airflow() -> None:
    """Embed recent Airflow task log snippets into airflow_embeddings.

    Fetches task instances with log_text populated for runs in the last 2 hours.
    Upserts (dag_id, dag_run_id, task_id, try_number) as the natural key.
    """
    pool = await get_pool()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)

    async with pool.acquire() as conn:
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

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO airflow_embeddings
                    (embedding, text, source_uri, dag_id, is_deleted)
                VALUES ($1::vector, $2, $3, $4, false)
                ON CONFLICT (source_uri) DO UPDATE
                    SET embedding=$1::vector, text=$2, is_deleted=false
                """,
                embedding, chunk_text, source_uri, row["dag_id"],
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_ingestion.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: Wire APScheduler into `main.py` lifespan**

Add the following to `backend/main.py` (inside the `lifespan` context, before `yield`):

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from ingestion.airflow_ingestor import ingest_airflow
from ingestion.quality_ingestor import ingest_quality

scheduler = AsyncIOScheduler()
scheduler.add_job(ingest_airflow, "interval", minutes=15, id="airflow_ingestor")
scheduler.add_job(ingest_quality, "interval", minutes=15, id="quality_ingestor")
scheduler.start()
```

And before the end of the `lifespan` context (after cancelling `_watchdog_task`):

```python
scheduler.shutdown(wait=False)
```

- [ ] **Step 6: Commit**

```bash
git add backend/ingestion/ backend/tests/test_ingestion.py backend/main.py
git commit -m "feat: ingestion pipeline (airflow embedder + quality ingestor writes live_metrics + history, APScheduler)"
```
