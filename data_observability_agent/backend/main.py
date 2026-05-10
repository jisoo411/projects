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
_mcp_healthy: bool = False  # set True after first successful tool load; watchdog maintains it


async def _start_mcp_subprocess() -> MultiServerMCPClient:
    """Start Airflow MCP server as a stdio subprocess and return the connected client."""
    return MultiServerMCPClient({
        "airflow": {
            "command": sys.executable,
            "args": ["-m", "airflow_mcp.server"],
            "transport": "stdio",
        }
    })


async def _load_mcp_tools(client: MultiServerMCPClient) -> list:
    return await client.get_tools()


async def _watchdog_loop(client: MultiServerMCPClient) -> None:
    """Ping the MCP server every 30 s; re-initialise on failure."""
    global _mcp_client, _mcp_healthy
    while True:
        await asyncio.sleep(30)
        try:
            tools = await client.get_tools()
            ping_tool = next((t for t in tools if t.name == "ping"), None)
            if ping_tool:
                await ping_tool.arun({})
            _mcp_healthy = True
        except Exception:
            _mcp_healthy = False
            _mcp_client = await _start_mcp_subprocess()
            new_tools = await _load_mcp_tools(_mcp_client)
            set_workflow_tools(new_tools)
            _mcp_healthy = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from ingestion.airflow_ingestor import ingest_airflow
    from ingestion.quality_ingestor import ingest_quality

    global _mcp_client, _watchdog_task
    _mcp_client = await _start_mcp_subprocess()
    tools = await _load_mcp_tools(_mcp_client)
    set_workflow_tools(tools)
    _mcp_healthy = True
    _watchdog_task = asyncio.create_task(_watchdog_loop(_mcp_client))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(ingest_airflow, "interval", minutes=15, id="airflow_ingestor")
    scheduler.add_job(ingest_quality, "interval", minutes=15, id="quality_ingestor")
    scheduler.start()

    yield

    scheduler.shutdown(wait=False)
    _watchdog_task.cancel()
    try:
        await _watchdog_task
    except asyncio.CancelledError:
        pass
    if _mcp_client:
        pass  # MultiServerMCPClient >= 0.1.0 manages subprocess lifecycle internally


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
