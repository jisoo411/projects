import pytest
import time
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI


def _make_app():
    from fastapi import FastAPI
    from api.chat import router
    app = FastAPI()
    app.include_router(router)
    return app


VALID_QUERY_BODY = {"query": "Why did orders_pipeline fail?"}
GOOD_GUARDRAIL = MagicMock(blocked=False, block_reason=None, redacted_query="Why did orders_pipeline fail?")
BLOCKED_GUARDRAIL = MagicMock(blocked=True, block_reason="off_topic", redacted_query="Why did orders_pipeline fail?")
GOOD_OUTPUT = MagicMock(
    blocked=False, block_reason=None,
    response="orders_pipeline failed. Sources: airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z"
)


@pytest.mark.asyncio
async def test_chat_streams_sse_response():
    with patch("api.chat.check_input", new=AsyncMock(return_value=GOOD_GUARDRAIL)), \
         patch("api.chat.run_orchestrator", new=AsyncMock(return_value=(GOOD_OUTPUT.response, False))), \
         patch("api.chat.check_output", return_value=GOOD_OUTPUT), \
         patch("api.chat._write_audit_log", new=AsyncMock()):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream("POST", "/chat",
                                     json=VALID_QUERY_BODY,
                                     headers={"X-API-Key": "test-key"}) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
    assert b"orders_pipeline" in body


@pytest.mark.asyncio
async def test_chat_input_guardrail_block_returns_400():
    with patch("api.chat.check_input", new=AsyncMock(return_value=BLOCKED_GUARDRAIL)), \
         patch("api.chat._write_audit_log", new=AsyncMock()):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={"query": "recipe?"},
                                     headers={"X-API-Key": "test-key"})
    assert resp.status_code == 400
    assert "off_topic" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_chat_requires_api_key():
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/chat", json=VALID_QUERY_BODY)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_rate_limit_blocks_after_10_requests():
    """11th request from the same API key within a minute returns 429."""
    import api.chat as chat_mod
    # Reset rate limit state to isolate test
    chat_mod._rate_limit_buckets.clear()
    good_result = MagicMock(blocked=False, block_reason=None, redacted_query="q")
    good_output = MagicMock(blocked=False, block_reason=None, response="r. Sources: airflow:dag=x/task=t/run=r/try=1/ts=2026-04-22T00:00:00Z")
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("api.chat.check_input", new=AsyncMock(return_value=good_result)), \
             patch("api.chat.run_orchestrator", new=AsyncMock(return_value=("r", False))), \
             patch("api.chat.check_output", return_value=good_output), \
             patch("api.chat._write_audit_log", new=AsyncMock()):
            for _ in range(10):
                await client.post("/chat", json=VALID_QUERY_BODY, headers={"X-API-Key": "rl-key"})
            resp = await client.post("/chat", json=VALID_QUERY_BODY, headers={"X-API-Key": "rl-key"})
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_audit_log_written_after_response():
    with patch("api.chat.check_input", new=AsyncMock(return_value=GOOD_GUARDRAIL)), \
         patch("api.chat.run_orchestrator", new=AsyncMock(return_value=(GOOD_OUTPUT.response, False))), \
         patch("api.chat.check_output", return_value=GOOD_OUTPUT), \
         patch("api.chat._write_audit_log", new=AsyncMock()) as mock_audit:
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream("POST", "/chat", json=VALID_QUERY_BODY,
                                     headers={"X-API-Key": "test-key"}) as resp:
                async for _ in resp.aiter_bytes():
                    pass
    mock_audit.assert_called_once()
