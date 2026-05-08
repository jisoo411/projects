import pytest
from unittest.mock import patch, AsyncMock

WORKFLOW_OUT = (
    "orders_pipeline failed at extract_orders — KeyError 'order_id'. "
    "Sources: airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z",
    False,
)
QUALITY_OUT = (
    "orders table quality score is 0.58 (warn). "
    "Sources: dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z",
    False,
)


@pytest.mark.asyncio
async def test_orchestrator_returns_text_and_fallback_flag():
    from agents.orchestrator import run_orchestrator
    with patch("agents.orchestrator.run_workflow_agent", new=AsyncMock(return_value=WORKFLOW_OUT)), \
         patch("agents.orchestrator.run_quality_agent", new=AsyncMock(return_value=QUALITY_OUT)), \
         patch("agents.orchestrator.ChatOpenAI") as mock_llm_cls:
        mock_llm = mock_llm_cls.return_value
        mock_llm.ainvoke = AsyncMock(return_value=type("R", (), {
            "content": "The orders pipeline failed due to a null key. Quality score is 0.58. "
                       "Sources: airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z, "
                       "dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z"
        })())
        text, fallback = await run_orchestrator("Why did orders_pipeline fail?")
    assert isinstance(text, str) and len(text) > 0
    assert fallback is False


@pytest.mark.asyncio
async def test_orchestrator_propagates_rerank_fallback():
    """If either sub-agent signals rerank_fallback=True, orchestrator propagates it."""
    from agents.orchestrator import run_orchestrator
    workflow_with_fallback = (WORKFLOW_OUT[0], True)
    with patch("agents.orchestrator.run_workflow_agent",
               new=AsyncMock(return_value=workflow_with_fallback)), \
         patch("agents.orchestrator.run_quality_agent", new=AsyncMock(return_value=QUALITY_OUT)), \
         patch("agents.orchestrator.ChatOpenAI") as mock_llm_cls:
        mock_llm = mock_llm_cls.return_value
        mock_llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": "result"})())
        _, fallback = await run_orchestrator("pipeline status?")
    assert fallback is True


@pytest.mark.asyncio
async def test_orchestrator_handles_workflow_agent_timeout():
    """If workflow agent times out, orchestrator still completes using quality output."""
    import asyncio
    from agents.orchestrator import run_orchestrator

    async def slow_workflow(_query):
        await asyncio.sleep(999)
        return ("never", False)

    with patch("agents.orchestrator.run_workflow_agent", new=slow_workflow), \
         patch("agents.orchestrator.run_quality_agent", new=AsyncMock(return_value=QUALITY_OUT)), \
         patch("agents.orchestrator.AGENT_TIMEOUT_S", 0.05), \
         patch("agents.orchestrator.ChatOpenAI") as mock_llm_cls:
        mock_llm = mock_llm_cls.return_value
        mock_llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": "partial result"})())
        text, _ = await run_orchestrator("data quality status?")
    assert isinstance(text, str) and len(text) > 0


@pytest.mark.asyncio
async def test_orchestrator_state_carries_both_agent_outputs():
    """Both sub-agent outputs are passed to the synthesis prompt."""
    from agents.orchestrator import run_orchestrator
    captured_prompt = {}

    async def fake_ainvoke(messages, **_kwargs):
        captured_prompt["messages"] = messages
        return type("R", (), {"content": "synthesis"})()

    with patch("agents.orchestrator.run_workflow_agent", new=AsyncMock(return_value=WORKFLOW_OUT)), \
         patch("agents.orchestrator.run_quality_agent", new=AsyncMock(return_value=QUALITY_OUT)), \
         patch("agents.orchestrator.ChatOpenAI") as mock_llm_cls:
        mock_llm = mock_llm_cls.return_value
        mock_llm.ainvoke = AsyncMock(side_effect=fake_ainvoke)
        await run_orchestrator("pipeline and quality summary?")

    prompt_str = str(captured_prompt.get("messages", ""))
    assert "orders_pipeline" in prompt_str
    assert "quality score" in prompt_str
