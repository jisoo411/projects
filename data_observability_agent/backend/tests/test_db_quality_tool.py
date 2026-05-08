import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def _make_pool(row_count=1000, null_count=25, max_ts="2026-04-22T01:00:00+00:00",
               schema_drift_col=None):
    """Build a mock asyncpg pool that returns controlled values."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=[row_count, null_count, max_ts, schema_drift_col])
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=conn)
    return pool


@pytest.mark.asyncio
async def test_compute_metrics_returns_all_fields():
    from tools.db_quality_tool import _compute_quality_metrics_async
    with patch("tools.db_quality_tool.get_pool", new=AsyncMock(return_value=_make_pool())):
        result = await _compute_quality_metrics_async("orders", "updated_at", "order_id")
    assert result["row_count"] == 1000
    assert result["null_rate"] == pytest.approx(0.025, abs=1e-4)
    assert result["freshness_hours"] >= 0.0
    assert result["schema_drift"] is False
    assert 0.0 <= result["score"] <= 1.0
    assert result["source_uri"].startswith("dbcheck:table=orders/metric=composite/ts=")


@pytest.mark.asyncio
async def test_high_null_rate_lowers_score():
    from tools.db_quality_tool import _compute_quality_metrics_async
    with patch("tools.db_quality_tool.get_pool",
               new=AsyncMock(return_value=_make_pool(null_count=800))):
        result = await _compute_quality_metrics_async("orders", "updated_at", "order_id")
    assert result["null_rate"] == pytest.approx(0.8, abs=1e-4)
    assert result["score"] < 0.5


@pytest.mark.asyncio
async def test_schema_drift_detected():
    from tools.db_quality_tool import _compute_quality_metrics_async
    with patch("tools.db_quality_tool.get_pool",
               new=AsyncMock(return_value=_make_pool(schema_drift_col="payload"))):
        result = await _compute_quality_metrics_async("orders", "updated_at", "order_id")
    assert result["schema_drift"] is True


@pytest.mark.asyncio
async def test_get_cached_metrics_reads_live_metrics_table():
    from tools.db_quality_tool import get_cached_metrics
    mock_row = {"table_name": "orders", "metric_type": "composite", "value": 0.92,
                "status": "ok", "observed_at": "2026-04-22T01:00:00+00:00",
                "source": "dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z"}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=mock_row)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=conn)
    with patch("tools.db_quality_tool.get_pool", new=AsyncMock(return_value=pool)):
        result = await get_cached_metrics("orders")
    assert result is not None
    assert result["value"] == 0.92


@pytest.mark.asyncio
async def test_get_cached_metrics_returns_none_when_absent():
    from tools.db_quality_tool import get_cached_metrics
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=conn)
    with patch("tools.db_quality_tool.get_pool", new=AsyncMock(return_value=pool)):
        result = await get_cached_metrics("unknown_table")
    assert result is None
