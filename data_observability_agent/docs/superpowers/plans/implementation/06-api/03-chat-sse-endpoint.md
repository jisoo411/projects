# Chat SSE Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `/chat` SSE streaming endpoint — runs input guardrails, dispatches the orchestrator, runs output guardrails, streams the response as SSE events, writes an `audit_log` row (PII-redacted query, guardrail outcome, response_ms), and enforces a token-bucket rate limit (10 req/min per API key).

**Architecture:** `StreamingResponse` yields SSE-formatted text chunks. Rate limit state stored in a module-level dict (reset per minute). `asyncio.CancelledError` is caught to cancel the in-flight LangGraph task and release DB connections. `audit_log` insert runs after the response is complete (non-blocking).

**Tech Stack:** FastAPI `StreamingResponse`, asyncio, asyncpg (shared pool)

---

## File Map

```
backend/
├── api/
│   └── chat.py
└── tests/
    └── test_chat.py
```

---

### Task 1: Write Failing Tests

**Files:**
- Create: `backend/tests/test_chat.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_chat.py
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_chat.py -v
```

Expected: FAIL — `api.chat` not found.

---

### Task 2: Implement the Chat Endpoint

**Files:**
- Create: `backend/api/chat.py`

- [ ] **Step 1: Write `backend/api/chat.py`**

```python
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
            "INSERT INTO audit_log (query_redacted, guardrail_outcome, response_ms, api_key_hash) "
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
        # Stream response in 256-char chunks
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
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_chat.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 3: Commit**

```bash
git add backend/api/chat.py backend/tests/test_chat.py
git commit -m "feat: /chat SSE endpoint (guardrails, orchestrator, token-bucket rate limit, audit_log)"
```
