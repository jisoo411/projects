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


def _make_pool_mock(table_rows=None, dag_rows=None):
    """Return a mock async pool whose acquire() yields a connection with fetch results."""
    table_rows = table_rows or []
    dag_rows = dag_rows or []

    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(side_effect=[table_rows, dag_rows])

    pool_mock = AsyncMock()
    pool_mock.acquire.return_value.__aenter__ = AsyncMock(return_value=conn_mock)
    pool_mock.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool_mock


@pytest.mark.asyncio
async def test_health_returns_200_when_all_ok():
    with patch("api.health._ping_mcp", new=MagicMock(return_value=True)), \
         patch("api.health._ping_db", new=AsyncMock(return_value=True)):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_returns_503_when_mcp_down():
    with patch("api.health._ping_mcp", new=MagicMock(return_value=False)), \
         patch("api.health._ping_db", new=AsyncMock(return_value=True)):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_health_returns_503_when_db_down():
    with patch("api.health._ping_mcp", new=MagicMock(return_value=True)), \
         patch("api.health._ping_db", new=AsyncMock(return_value=False)):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_status_returns_live_metrics_for_all_tables():
    table_row = MagicMock()
    table_row.keys = MagicMock(return_value=["table_name", "metric_type", "value", "status", "observed_at", "source"])
    table_row.__iter__ = MagicMock(return_value=iter([
        ("table_name", "orders"), ("metric_type", "composite"), ("value", 0.92),
        ("status", "ok"), ("observed_at", "2026-04-22T01:00:00+00:00"),
        ("source", "dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z"),
    ]))

    with patch("api.status.get_pool", new=AsyncMock(return_value=_make_pool_mock(
        table_rows=[table_row], dag_rows=[]
    ))), patch("api.status.get_source_pool", new=AsyncMock(return_value=_make_pool_mock(
        table_rows=[], dag_rows=[]
    ))):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/status")
    assert resp.status_code == 200
    assert "tables" in resp.json()
    assert "dags" in resp.json()


@pytest.mark.asyncio
async def test_status_includes_dag_rows():
    dag_row = MagicMock()
    dag_row.keys = MagicMock(return_value=["dag_id", "state", "observed_at"])
    dag_row.__iter__ = MagicMock(return_value=iter([
        ("dag_id", "nasa_neo_ingest"), ("state", "success"),
        ("observed_at", "2026-04-22T02:00:00+00:00"),
    ]))

    with patch("api.status.get_pool", new=AsyncMock(return_value=_make_pool_mock(
        table_rows=[], dag_rows=[]
    ))), patch("api.status.get_source_pool", new=AsyncMock(return_value=_make_pool_mock(
        table_rows=[], dag_rows=[dag_row]
    ))):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/status")
    assert resp.status_code == 200
    assert "dags" in resp.json()
