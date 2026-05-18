# Data Quality Sub-Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Data Quality Sub-Agent that reads from the `live_metrics` cache by default and triggers on-demand live DB checks only when a specific table is explicitly named in the query. Uses an asyncio semaphore to prevent DB pool starvation under concurrent load.

**Architecture:** Cache reads via `get_cached_metrics()` are fast and non-blocking. On-demand live checks via `get_quality_tools()` are gated by a semaphore (`MAX_CONCURRENT_LIVE_CHECKS=3`). RAG retrieves from `quality_embeddings` using `retrieve(query, table="quality_embeddings", filter_col="table_name", filter_val=detected_table_name)`. Returns `(response_text, rerank_fallback)`.

**Tech Stack:** LangChain, langchain-openai, OpenAI GPT-4o, asyncpg

---

## File Map

```
backend/
├── agents/
│   └── quality_agent.py
└── tests/
    └── test_quality_agent.py
```

---

### Task 1: Write Failing Tests

**Files:**
- Create: `backend/tests/test_quality_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_quality_agent.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

MOCK_QE_CHUNK = {
    "id": "uuid-q1",
    "text": "Table: orders\nNull rate: 25.0%\nRow count: 1000\nScore: 0.58",
    "source_uri": "dbcheck:table=orders/metric=null_rate/ts=2026-04-22T01:00:00Z",
    "rerank_score": 0.88,
}
MOCK_CACHED_METRIC = {
    "table_name": "orders", "metric_type": "composite",
    "value": 0.58, "status": "warn",
    "observed_at": "2026-04-22T01:00:00+00:00",
    "source": "dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z",
}

@pytest.mark.asyncio
async def test_quality_agent_reads_cache_for_general_query():
    """General query (no specific table named) reads from live_metrics cache."""
    from agents.quality_agent import run_quality_agent
    with patch("agents.quality_agent.get_cached_metrics",
               new=AsyncMock(return_value=MOCK_CACHED_METRIC)), \
         patch("agents.quality_agent.retrieve",
               new=AsyncMock(return_value=([MOCK_QE_CHUNK], False))), \
         patch("agents.quality_agent.AgentExecutor") as mock_exec_cls:
        mock_exec_cls.return_value = AsyncMock(
            ainvoke=AsyncMock(return_value={
                "output": "orders table quality score is 0.58. Sources: dbcheck:table=orders/metric=null_rate/ts=2026-04-22T01:00:00Z"
            })
        )
        text, fallback = await run_quality_agent("Any data quality issues?")
    assert "orders" in text
    assert fallback is False

@pytest.mark.asyncio
async def test_quality_agent_returns_text_and_fallback_flag():
    from agents.quality_agent import run_quality_agent
    with patch("agents.quality_agent.get_cached_metrics",
               new=AsyncMock(return_value=MOCK_CACHED_METRIC)), \
         patch("agents.quality_agent.retrieve",
               new=AsyncMock(return_value=([MOCK_QE_CHUNK], True))), \
         patch("agents.quality_agent.AgentExecutor") as mock_exec_cls:
        mock_exec_cls.return_value = AsyncMock(
            ainvoke=AsyncMock(return_value={
                "output": "quality result. Sources: dbcheck:table=orders/metric=null_rate/ts=2026-04-22T01:00:00Z"
            })
        )
        _, fallback = await run_quality_agent("quality status?")
    assert fallback is True

@pytest.mark.asyncio
async def test_quality_agent_detects_table_name():
    """Agent extracts table_name from query for prefiltering."""
    from agents.quality_agent import _extract_table_name
    assert _extract_table_name("Are there issues with the orders table?") == "orders"
    assert _extract_table_name("check users data quality") == "users"
    assert _extract_table_name("general pipeline health") is None

@pytest.mark.asyncio
async def test_quality_agent_semaphore_limits_concurrent_live_checks():
    """On-demand live checks are gated by MAX_CONCURRENT_LIVE_CHECKS semaphore."""
    import asyncio
    from agents.quality_agent import _live_check_sem
    # The semaphore should have the configured limit (default 3)
    from config import settings
    assert _live_check_sem._value == settings.max_concurrent_live_checks
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_quality_agent.py -v
```

Expected: FAIL — `agents.quality_agent` not found.

---

### Task 2: Implement the Quality Sub-Agent

**Files:**
- Create: `backend/agents/quality_agent.py`

- [ ] **Step 1: Write `backend/agents/quality_agent.py`**

```python
import asyncio
import re
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from config import settings
from rag.retriever import retrieve
from tools.db_quality_tool import get_quality_tools, get_cached_metrics

# Semaphore: cap concurrent on-demand live DB checks to prevent pool starvation
_live_check_sem = asyncio.Semaphore(settings.max_concurrent_live_checks)

_MONITORED_TABLES = ["orders", "users", "inventory_items", "revenue_aggregate"]

def _extract_table_name(query: str) -> str | None:
    """Detect an explicitly named monitored table in the query."""
    q_lower = query.lower()
    for table in _MONITORED_TABLES:
        if table.replace("_", " ") in q_lower or table in q_lower:
            return table
    return None

SYSTEM_PROMPT = """You are a data quality monitoring assistant for a data warehouse.

Cached quality summary (from last 15-min ingestion cycle):
{cached_context}

Relevant historical quality records:
{rag_context}

Rules:
- A quality score below 0.7 indicates a problem requiring attention.
- Always report: table name, affected metric, metric value, and when it was observed.
- Use source URIs in the format: dbcheck:table={{table}}/metric={{metric}}/ts={{ts}}
- If asked about a specific table and the cache is stale (>15 min), note it.
- If you cannot determine the root cause, say so explicitly.
- End with a "Sources:" section listing each dbcheck: URI you used."""

async def run_quality_agent(query: str) -> tuple[str, bool]:
    """Run the data quality sub-agent.

    Reads live_metrics cache for general queries.
    Triggers on-demand live check (gated by semaphore) when a specific table is named.
    Returns (response_text, rerank_fallback).
    """
    table_name = _extract_table_name(query)

    # Build cached context for all monitored tables
    cached_lines: list[str] = []
    for tbl in _MONITORED_TABLES:
        cached = await get_cached_metrics(tbl)
        if cached:
            cached_lines.append(
                f"- {tbl}: score={cached.get('value','?')}, status={cached.get('status','?')}, "
                f"observed={cached.get('observed_at','?')}"
            )
    cached_context = "\n".join(cached_lines) or "No cache data yet (first ingestion cycle pending)."

    # Determine tools: include live-check tool only when a specific table is named
    if table_name:
        async with _live_check_sem:
            tools = get_quality_tools()
    else:
        tools = []

    chunks, rerank_fallback = await retrieve(
        query,
        table="quality_embeddings",
        filter_col="table_name" if table_name else None,
        filter_val=table_name,
    )
    rag_context = "\n\n".join(
        f"[Source: {c['source_uri']}]\n{c['text']}" for c in chunks
    ) or "No relevant historical quality records found."

    llm = ChatOpenAI(
        model="gpt-4o", temperature=0, max_tokens=2048,
        openai_api_key=settings.openai_api_key,
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ]).partial(cached_context=cached_context, rag_context=rag_context)

    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, max_iterations=5,
                             handle_parsing_errors=True)
    result = await executor.ainvoke({"input": query})
    return result["output"], rerank_fallback
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_quality_agent.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 3: Commit**

```bash
git add backend/agents/quality_agent.py backend/tests/test_quality_agent.py
git commit -m "feat: data quality sub-agent (live_metrics cache, on-demand checks, semaphore)"
```
