# FastAPI Application Entry Point Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FastAPI application entry point (`main.py`) — manages lifespan (startup/shutdown), launches the Airflow MCP stdio subprocess, runs the MCP watchdog background task (pings every 30s), injects MCP tools into the workflow agent via `set_workflow_tools()`, and configures restricted CORS using `frontend_origin` from config.

**Architecture:** `asynccontextmanager` lifespan: starts `airflow_mcp/server.py` as a stdio subprocess via `langchain-mcp-adapters`, starts the watchdog task, calls `set_workflow_tools(tools)`. On shutdown: cancels watchdog, terminates subprocess. CORS restricts `allow_origins` to `[settings.frontend_origin]` — never wildcard.

**Tech Stack:** FastAPI, langchain-mcp-adapters, asyncio subprocess

---

## File Map

```
backend/
├── main.py
└── tests/
    └── test_main.py
```

---

### Task 1: Write Failing Tests

**Files:**
- Create: `backend/tests/test_main.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_main.py
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_main.py -v
```

Expected: FAIL — `main` module not found or lifespan not wired.

---

### Task 2: Implement `main.py`

**Files:**
- Create: `backend/main.py`

- [ ] **Step 1: Write `backend/main.py`**

```python
import asyncio
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient
from config import settings
from agents.workflow_agent import set_workflow_tools

_mcp_client: MultiServerMCPClient | None = None
_watchdog_task: asyncio.Task | None = None

async def _start_mcp_subprocess():
    """Start the Airflow MCP server as a stdio subprocess and return the client."""
    client = MultiServerMCPClient({
        "airflow": {
            "command": sys.executable,
            "args": ["-m", "airflow_mcp.server"],
            "transport": "stdio",
        }
    })
    await client.__aenter__()
    return client

async def _load_mcp_tools(client: MultiServerMCPClient) -> list:
    return client.get_tools()

async def _watchdog_loop(client: MultiServerMCPClient) -> None:
    """Ping the MCP server every 30 s; re-initialise on failure."""
    global _mcp_client
    while True:
        await asyncio.sleep(30)
        try:
            tools = client.get_tools()
            ping_tool = next((t for t in tools if t.name == "ping"), None)
            if ping_tool:
                await ping_tool.arun({})
        except Exception:
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass
            _mcp_client = await _start_mcp_subprocess()
            new_tools = await _load_mcp_tools(_mcp_client)
            set_workflow_tools(new_tools)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mcp_client, _watchdog_task
    _mcp_client = await _start_mcp_subprocess()
    tools = await _load_mcp_tools(_mcp_client)
    set_workflow_tools(tools)
    _watchdog_task = asyncio.create_task(_watchdog_loop(_mcp_client))
    yield
    _watchdog_task.cancel()
    try:
        await _watchdog_task
    except asyncio.CancelledError:
        pass
    if _mcp_client:
        await _mcp_client.__aexit__(None, None, None)

app = FastAPI(title="Pipeline Observability API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers registered in separate modules — imported here to attach routes
from api.health import router as health_router      # noqa: E402
from api.status import router as status_router      # noqa: E402
from api.chat import router as chat_router          # noqa: E402

app.include_router(health_router)
app.include_router(status_router)
app.include_router(chat_router)
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_main.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 3: Commit**

```bash
git add backend/main.py backend/tests/test_main.py
git commit -m "feat: FastAPI app entry point (MCP lifespan, watchdog, set_workflow_tools injection, restricted CORS)"
```
