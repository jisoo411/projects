import pytest
from unittest.mock import patch, AsyncMock, MagicMock

MOCK_QE_CHUNK = {
    "id": "uuid-q1",
    "text": "Table: orders\nNull rate: 25.0%\nRow count: 1000\nScore: 0.58",
    "source_uri": "dbcheck:table=orders/metric=null_rate/ts=2026-04-22T01:00:00Z",
    "rerank_score": 0.88,
}
MOCK_CACHED_METRIC = {
    "table_name": "orders", "metric_type": "composite",
    "value": 0.58, "status": "warn",
    "observed_at": "2026-04-22T01:00:00+00:00",
    "source": "dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z",
}


@pytest.mark.asyncio
async def test_quality_agent_reads_cache_for_general_query():
    """General query (no specific table named) reads from live_metrics cache."""
    from agents.quality_agent import run_quality_agent
    with patch("agents.quality_agent.get_cached_metrics",
               new=AsyncMock(return_value=MOCK_CACHED_METRIC)), \
         patch("agents.quality_agent.retrieve",
               new=AsyncMock(return_value=([MOCK_QE_CHUNK], False))), \
         patch("agents.quality_agent.AgentExecutor") as mock_exec_cls:
        mock_exec_cls.return_value = AsyncMock(
            ainvoke=AsyncMock(return_value={
                "output": "orders table quality score is 0.58. Sources: dbcheck:table=orders/metric=null_rate/ts=2026-04-22T01:00:00Z"
            })
        )
        text, fallback = await run_quality_agent("Any data quality issues?")
    assert "orders" in text
    assert fallback is False


@pytest.mark.asyncio
async def test_quality_agent_returns_text_and_fallback_flag():
    from agents.quality_agent import run_quality_agent
    with patch("agents.quality_agent.get_cached_metrics",
               new=AsyncMock(return_value=MOCK_CACHED_METRIC)), \
         patch("agents.quality_agent.retrieve",
               new=AsyncMock(return_value=([MOCK_QE_CHUNK], True))), \
         patch("agents.quality_agent.AgentExecutor") as mock_exec_cls:
        mock_exec_cls.return_value = AsyncMock(
            ainvoke=AsyncMock(return_value={
                "output": "quality result. Sources: dbcheck:table=orders/metric=null_rate/ts=2026-04-22T01:00:00Z"
            })
        )
        _, fallback = await run_quality_agent("quality status?")
    assert fallback is True


@pytest.mark.asyncio
async def test_quality_agent_detects_table_name():
    """Agent extracts table_name from query for prefiltering."""
    from agents.quality_agent import _extract_table_name
    assert _extract_table_name("Are there issues with the orders table?") == "orders"
    assert _extract_table_name("check users data quality") == "users"
    assert _extract_table_name("general pipeline health") is None


@pytest.mark.asyncio
async def test_quality_agent_semaphore_limits_concurrent_live_checks():
    """On-demand live checks are gated by MAX_CONCURRENT_LIVE_CHECKS semaphore."""
    from agents.quality_agent import _live_check_sem
    from config import settings
    assert _live_check_sem._value == settings.max_concurrent_live_checks
