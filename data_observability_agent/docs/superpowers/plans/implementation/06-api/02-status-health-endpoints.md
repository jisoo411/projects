# Status and Health Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/health` and `/status` endpoints. `/health` pings the MCP subprocess and the database, returning 200 OK or 503. `/status` reads `live_metrics` for the monitored tables, falls back to `dag_status_cache` for DAG state when the MCP subprocess is unavailable, and returns a structured dashboard payload.

**Architecture:** Both endpoints live in separate router modules (`api/health.py`, `api/status.py`). `/health` uses a short timeout (5 s) for the MCP ping. `/status` calls `get_cached_metrics()` for each monitored table and reads `dag_status_cache` rows directly via the shared asyncpg pool.

**Tech Stack:** FastAPI `APIRouter`, asyncpg (shared pool from `rag.retriever`), asyncio timeout

---

## File Map

```
backend/
├── api/
│   ├── __init__.py      # empty package marker
│   ├── health.py
│   └── status.py
└── tests/
    └── test_status_health.py
```

---

### Task 1: Write Failing Tests

**Files:**
- Create: `backend/tests/test_status_health.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_status_health.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

def _make_app():
    """Build a minimal FastAPI app with only health and status routers (no MCP lifespan)."""
    from fastapi import FastAPI
    from api.health import router as health_router
    from api.status import router as status_router
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(status_router)
    return app

MOCK_CACHED = {
    "table_name": "orders", "metric_type": "composite", "value": 0.92,
    "status": "ok", "observed_at": "2026-04-22T01:00:00+00:00",
    "source": "dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z",
}

@pytest.mark.asyncio
async def test_health_returns_200_when_all_ok():
    with patch("api.health._ping_mcp", new=AsyncMock(return_value=True)), \
         patch("api.health._ping_db", new=AsyncMock(return_value=True)):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_health_returns_503_when_mcp_down():
    with patch("api.health._ping_mcp", new=AsyncMock(return_value=False)), \
         patch("api.health._ping_db", new=AsyncMock(return_value=True)):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
    assert resp.status_code == 503

@pytest.mark.asyncio
async def test_health_returns_503_when_db_down():
    with patch("api.health._ping_mcp", new=AsyncMock(return_value=True)), \
         patch("api.health._ping_db", new=AsyncMock(return_value=False)):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
    assert resp.status_code == 503

@pytest.mark.asyncio
async def test_status_returns_live_metrics_for_all_tables():
    with patch("api.status.get_cached_metrics", new=AsyncMock(return_value=MOCK_CACHED)), \
         patch("api.status._get_dag_status_cache", new=AsyncMock(return_value=[])):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "tables" in data
    assert any(t["table_name"] == "orders" for t in data["tables"])

@pytest.mark.asyncio
async def test_status_includes_dag_cache():
    dag_row = {"dag_id": "orders_pipeline", "state": "failed", "observed_at": "2026-04-22T02:00:00+00:00"}
    with patch("api.status.get_cached_metrics", new=AsyncMock(return_value=None)), \
         patch("api.status._get_dag_status_cache", new=AsyncMock(return_value=[dag_row])):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "dags" in data
    assert any(d["dag_id"] == "orders_pipeline" for d in data["dags"])
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_status_health.py -v
```

Expected: FAIL — `api.health` and `api.status` not found.

---

### Task 2: Implement the Endpoints

**Files:**
- Create: `backend/api/__init__.py`
- Create: `backend/api/health.py`
- Create: `backend/api/status.py`

- [ ] **Step 1: Write `backend/api/__init__.py`**

```python
```

(Empty — package marker only.)

- [ ] **Step 2: Write `backend/api/health.py`**

```python
import asyncio
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from rag.retriever import get_pool
import main as app_module

router = APIRouter()

async def _ping_mcp() -> bool:
    """Call the MCP ping tool with a 5-second hard deadline."""
    client = app_module._mcp_client
    if client is None:
        return False
    try:
        tools = client.get_tools()
        ping_tool = next((t for t in tools if t.name == "ping"), None)
        if ping_tool is None:
            return False
        await asyncio.wait_for(ping_tool.arun({}), timeout=5.0)
        return True
    except Exception:
        return False

async def _ping_db() -> bool:
    try:
        pool = await asyncio.wait_for(get_pool(), timeout=5.0)
        async with pool.acquire() as conn:
            await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=5.0)
        return True
    except Exception:
        return False

@router.get("/health")
async def health():
    mcp_ok, db_ok = await asyncio.gather(_ping_mcp(), _ping_db())
    if mcp_ok and db_ok:
        return {"status": "ok", "mcp": "ok", "db": "ok"}
    body = {
        "status": "degraded",
        "mcp": "ok" if mcp_ok else "down",
        "db": "ok" if db_ok else "down",
    }
    return JSONResponse(status_code=503, content=body)
```

- [ ] **Step 3: Write `backend/api/status.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_status_health.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/api/ backend/tests/test_status_health.py
git commit -m "feat: /health (MCP+DB ping, 503 on failure) and /status (live_metrics + dag_status_cache)"
```
