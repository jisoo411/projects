import asyncio
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from rag.retriever import get_pool

router = APIRouter()


async def _ping_mcp() -> bool:
    """Call the MCP ping tool with a 5-second hard deadline."""
    import main as app_module  # lazy — avoids circular import at load time
    client = app_module._mcp_client
    if client is None:
        return False
    try:
        tools = await client.get_tools()
        ping_tool = next((t for t in tools if t.name == "ping"), None)
        if ping_tool is None:
            return False
        await asyncio.wait_for(ping_tool.arun({}), timeout=5.0)
        return True
    except Exception:
        return False


async def _ping_db() -> bool:
    try:
        pool = await asyncio.wait_for(get_pool(), timeout=5.0)
        async with pool.acquire() as conn:
            await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=5.0)
        return True
    except Exception:
        return False


@router.get("/health")
async def health():
    mcp_ok, db_ok = await asyncio.gather(_ping_mcp(), _ping_db())
    if mcp_ok and db_ok:
        return {"status": "ok", "mcp": "ok", "db": "ok"}
    body = {
        "status": "degraded",
        "mcp": "ok" if mcp_ok else "down",
        "db": "ok" if db_ok else "down",
    }
    return JSONResponse(status_code=503, content=body)
