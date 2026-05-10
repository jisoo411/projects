import pytest
from unittest.mock import patch, AsyncMock, MagicMock

MOCK_CHUNK = {
    "id": "uuid-1",
    "text": "orders_pipeline failed at task extract_orders — KeyError 'order_id'",
    "source_uri": "airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z",
    "rerank_score": 0.91,
}


@pytest.mark.asyncio
async def test_workflow_agent_returns_text_and_fallback_flag():
    from agents.workflow_agent import run_workflow_agent, set_workflow_tools
    set_workflow_tools([])
    with patch("agents.workflow_agent.retrieve",
               new=AsyncMock(return_value=([MOCK_CHUNK], False))), \
         patch("agents.workflow_agent.AgentExecutor") as mock_exec_cls:
        mock_exec_cls.return_value = AsyncMock(
            ainvoke=AsyncMock(return_value={"output": "orders_pipeline failed due to null key. Sources: airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z"})
        )
        text, fallback = await run_workflow_agent("Why did orders_pipeline fail?")
    assert "orders_pipeline" in text
    assert fallback is False


@pytest.mark.asyncio
async def test_workflow_agent_propagates_rerank_fallback():
    from agents.workflow_agent import run_workflow_agent, set_workflow_tools
    set_workflow_tools([])
    with patch("agents.workflow_agent.retrieve",
               new=AsyncMock(return_value=([MOCK_CHUNK], True))), \
         patch("agents.workflow_agent.AgentExecutor") as mock_exec_cls:
        mock_exec_cls.return_value = AsyncMock(
            ainvoke=AsyncMock(return_value={"output": "result. Sources: airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z"})
        )
        _, fallback = await run_workflow_agent("status?")
    assert fallback is True


@pytest.mark.asyncio
async def test_workflow_agent_detects_dag_id_for_prefilter():
    """Agent extracts dag_id from query and passes it as filter_val to retrieve()."""
    from agents.workflow_agent import run_workflow_agent, set_workflow_tools, _extract_dag_id
    set_workflow_tools([])
    assert _extract_dag_id("Why did orders_pipeline fail?") == "orders_pipeline"
    assert _extract_dag_id("what is wrong") is None


@pytest.mark.asyncio
async def test_workflow_agent_handles_empty_rag_results():
    from agents.workflow_agent import run_workflow_agent, set_workflow_tools
    set_workflow_tools([])
    with patch("agents.workflow_agent.retrieve", new=AsyncMock(return_value=([], False))), \
         patch("agents.workflow_agent.AgentExecutor") as mock_exec_cls:
        mock_exec_cls.return_value = AsyncMock(
            ainvoke=AsyncMock(return_value={"output": "No relevant history found. Sources: none"})
        )
        text, _ = await run_workflow_agent("unknown dag status?")
    assert isinstance(text, str) and len(text) > 0
