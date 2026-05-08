import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient

from agents.workflow_agent import set_workflow_tools
from config import settings

_mcp_client: MultiServerMCPClient | None = None
_watchdog_task: asyncio.Task | None = None


async def _start_mcp_subprocess() -> MultiServerMCPClient:
    """Start Airflow MCP server as a stdio subprocess and return the connected client."""
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

from api.chat import router as chat_router      # noqa: E402
from api.health import router as health_router  # noqa: E402
from api.status import router as status_router  # noqa: E402

app.include_router(health_router)
app.include_router(status_router)
app.include_router(chat_router)
