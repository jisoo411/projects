import asyncio
import hmac
import hashlib
import time
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import StreamingResponse
from models import ChatRequest
from guardrails.input_guardrails import check_input
from guardrails.output_guardrails import check_output
from agents.orchestrator import run_orchestrator
from rag.retriever import get_pool
from config import settings

router = APIRouter()

# Token bucket: {api_key: (request_count, window_start_epoch_s)}
_rate_limit_buckets: dict[str, tuple[int, float]] = {}
_RATE_LIMIT = 10
_RATE_WINDOW = 60.0


def _check_rate_limit(api_key: str) -> bool:
    """Return True if the request is allowed; False if the limit is exceeded."""
    now = time.monotonic()
    count, window_start = _rate_limit_buckets.get(api_key, (0, now))
    if now - window_start >= _RATE_WINDOW:
        count, window_start = 0, now
    if count >= _RATE_LIMIT:
        return False
    _rate_limit_buckets[api_key] = (count + 1, window_start)
    return True


def _audit_key_hash(api_key: str) -> str:
    """HMAC-truncated key for audit log (never stores the raw key)."""
    return hmac.new(
        settings.audit_hmac_secret.encode(),
        api_key.encode(),
        hashlib.sha256,
    ).hexdigest()[:16]


async def _write_audit_log(
    redacted_query: str,
    guardrail_outcome: str,
    response_ms: int,
    api_key_hash: str,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO audit_log (query_text, guardrail_outcome, response_ms, api_key_id) "
            "VALUES ($1, $2, $3, $4)",
            redacted_query, guardrail_outcome, response_ms, api_key_hash,
        )


async def _stream_chat(query: str, api_key: str):
    t0 = time.monotonic()
    api_key_hash = _audit_key_hash(api_key)
    guardrail_result = await check_input(query)

    if guardrail_result.blocked:
        response_ms = int((time.monotonic() - t0) * 1000)
        await _write_audit_log(
            guardrail_result.redacted_query,
            guardrail_result.block_reason or "blocked",
            response_ms,
            api_key_hash,
        )
        raise HTTPException(status_code=400, detail=guardrail_result.block_reason)

    orchestrator_task = asyncio.create_task(run_orchestrator(query))
    try:
        response_text, rerank_fallback = await orchestrator_task
    except asyncio.CancelledError:
        orchestrator_task.cancel()
        raise

    output_result = check_output(response_text, context=query)
    if output_result.blocked:
        response_ms = int((time.monotonic() - t0) * 1000)
        await _write_audit_log(
            guardrail_result.redacted_query,
            output_result.block_reason or "output_blocked",
            response_ms,
            api_key_hash,
        )
        raise HTTPException(status_code=500, detail="Response failed output validation.")

    response_ms = int((time.monotonic() - t0) * 1000)
    degraded_flag = "data: [DEGRADED_RERANKING]\n\n" if rerank_fallback else ""

    async def event_generator():
        if degraded_flag:
            yield degraded_flag
        text = output_result.response
        for i in range(0, len(text), 256):
            yield f"data: {text[i:i+256]}\n\n"
        yield "data: [DONE]\n\n"
        await _write_audit_log(
            guardrail_result.redacted_query, "ok", response_ms, api_key_hash
        )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/chat")
async def chat(
    request: ChatRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required.")
    if not _check_rate_limit(x_api_key):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 10 requests/min.")
    return await _stream_chat(request.query, x_api_key)
