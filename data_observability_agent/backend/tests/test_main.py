import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_cors_header_present_for_allowed_origin():
    """Response includes Access-Control-Allow-Origin for the configured frontend origin."""
    with patch("main._start_mcp_subprocess", new=AsyncMock(return_value=MagicMock())), \
         patch("main._load_mcp_tools", new=AsyncMock(return_value=[])), \
         patch("main._watchdog_loop", new=AsyncMock()):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.options(
                "/health",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


@pytest.mark.asyncio
async def test_cors_does_not_allow_arbitrary_origin():
    """Arbitrary origins are NOT echoed back (no wildcard CORS)."""
    with patch("main._start_mcp_subprocess", new=AsyncMock(return_value=MagicMock())), \
         patch("main._load_mcp_tools", new=AsyncMock(return_value=[])), \
         patch("main._watchdog_loop", new=AsyncMock()):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.options(
                "/health",
                headers={
                    "Origin": "http://evil.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert resp.headers.get("access-control-allow-origin") != "http://evil.com"


@pytest.mark.asyncio
async def test_workflow_tools_injected_at_startup():
    """set_workflow_tools() is called during lifespan startup."""
    mock_tools = [MagicMock()]
    with patch("main._start_mcp_subprocess", new=AsyncMock(return_value=MagicMock())), \
         patch("main._load_mcp_tools", new=AsyncMock(return_value=mock_tools)), \
         patch("main._watchdog_loop", new=AsyncMock()), \
         patch("main.set_workflow_tools") as mock_set:
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as _:
            pass
        mock_set.assert_called_once_with(mock_tools)
