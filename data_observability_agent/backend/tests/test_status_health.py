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
