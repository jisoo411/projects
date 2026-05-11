import asyncio
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from rag.retriever import get_pool

router = APIRouter()


def _ping_mcp() -> bool:
    """Check MCP health via the watchdog-maintained flag — no subprocess I/O."""
    import main as app_module  # lazy — avoids circular import at load time
    return app_module._mcp_client is not None and app_module._mcp_healthy


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
    mcp_ok = _ping_mcp()
    db_ok = await _ping_db()
    if mcp_ok and db_ok:
        return {"status": "ok", "mcp": "ok", "db": "ok"}
    body = {
        "status": "degraded",
        "mcp": "ok" if mcp_ok else "down",
        "db": "ok" if db_ok else "down",
    }
    return JSONResponse(status_code=503, content=body)
