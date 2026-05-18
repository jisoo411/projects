# Workflow Sub-Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Workflow Sub-Agent that queries live Airflow data via MCP tools and augments responses with hybrid RAG over `airflow_embeddings`. Detects `dag_id` in the query for pre-filtering. Returns a `(response_text, rerank_fallback)` tuple.

**Architecture:** MCP tools are loaded once at app startup and injected via `set_workflow_tools()`. The agent uses LangChain's `AgentExecutor` with `create_tool_calling_agent`. RAG retrieves from `airflow_embeddings` using `retrieve(query, table="airflow_embeddings", filter_col="dag_id", filter_val=detected_dag_id)`. Source URIs from retrieved chunks are included in the synthesis prompt so the LLM cites them in the `airflow:dag=...` format.

**Tech Stack:** LangChain, langchain-openai, OpenAI GPT-4o

---

## File Map

```
backend/
├── agents/
│   └── workflow_agent.py
└── tests/
    └── test_workflow_agent.py
```

---

### Task 1: Write Failing Tests

**Files:**
- Create: `backend/tests/test_workflow_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_workflow_agent.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

MOCK_CHUNK = {
    "id": "uuid-1",
    "text": "orders_pipeline failed at task extract_orders — KeyError 'order_id'",
    "source_uri": "airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z",
    "rerank_score": 0.91,
}

@pytest.mark.asyncio
async def test_workflow_agent_returns_text_and_fallback_flag():
    from agents.workflow_agent import run_workflow_agent, set_workflow_tools
    set_workflow_tools([])
    with patch("agents.workflow_agent.retrieve",
               new=AsyncMock(return_value=([MOCK_CHUNK], False))), \
         patch("agents.workflow_agent.AgentExecutor") as mock_exec_cls:
        mock_exec_cls.return_value = AsyncMock(
            ainvoke=AsyncMock(return_value={"output": "orders_pipeline failed due to null key. Sources: airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z"})
        )
        text, fallback = await run_workflow_agent("Why did orders_pipeline fail?")
    assert "orders_pipeline" in text
    assert fallback is False

@pytest.mark.asyncio
async def test_workflow_agent_propagates_rerank_fallback():
    from agents.workflow_agent import run_workflow_agent, set_workflow_tools
    set_workflow_tools([])
    with patch("agents.workflow_agent.retrieve",
               new=AsyncMock(return_value=([MOCK_CHUNK], True))), \
         patch("agents.workflow_agent.AgentExecutor") as mock_exec_cls:
        mock_exec_cls.return_value = AsyncMock(
            ainvoke=AsyncMock(return_value={"output": "result. Sources: airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z"})
        )
        _, fallback = await run_workflow_agent("status?")
    assert fallback is True

@pytest.mark.asyncio
async def test_workflow_agent_detects_dag_id_for_prefilter():
    """Agent extracts dag_id from query and passes it as filter_val to retrieve()."""
    from agents.workflow_agent import run_workflow_agent, set_workflow_tools, _extract_dag_id
    set_workflow_tools([])
    assert _extract_dag_id("Why did orders_pipeline fail?") == "orders_pipeline"
    assert _extract_dag_id("what is wrong") is None

@pytest.mark.asyncio
async def test_workflow_agent_handles_empty_rag_results():
    from agents.workflow_agent import run_workflow_agent, set_workflow_tools
    set_workflow_tools([])
    with patch("agents.workflow_agent.retrieve", new=AsyncMock(return_value=([], False))), \
         patch("agents.workflow_agent.AgentExecutor") as mock_exec_cls:
        mock_exec_cls.return_value = AsyncMock(
            ainvoke=AsyncMock(return_value={"output": "No relevant history found. Sources: none"})
        )
        text, _ = await run_workflow_agent("unknown dag status?")
    assert isinstance(text, str) and len(text) > 0
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_workflow_agent.py -v
```

Expected: FAIL — `agents.workflow_agent` not found.

---

### Task 2: Implement the Workflow Sub-Agent

**Files:**
- Create: `backend/agents/workflow_agent.py`

- [ ] **Step 1: Write `backend/agents/workflow_agent.py`**

```python
import re
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from config import settings
from rag.retriever import retrieve

# MCP tools injected at startup via set_workflow_tools(); avoids circular import with main.py
_workflow_tools: list = []

def set_workflow_tools(tools: list) -> None:
    global _workflow_tools
    _workflow_tools = tools

# Known DAG IDs for detection — extended during setup
_KNOWN_DAG_IDS = ["orders_pipeline", "user_sync_dag", "inventory_load", "revenue_agg"]

def _extract_dag_id(query: str) -> str | None:
    """Lightweight dag_id extractor: checks known IDs first, then regex patterns."""
    q_lower = query.lower()
    for dag_id in _KNOWN_DAG_IDS:
        if dag_id.lower() in q_lower:
            return dag_id
    # Fallback: snake_case token that looks like a DAG name
    match = re.search(r"\b([a-z][a-z0-9_]+_(?:dag|pipeline|flow|job|sync|load|agg))\b", q_lower)
    return match.group(1) if match else None

SYSTEM_PROMPT = """You are a workflow monitoring assistant with access to Apache Airflow.
Use the Airflow tools to fetch live pipeline status when the user asks about current state.
When get_task_log returns truncated=true, tell the user and include the source link.

Relevant historical context from past runs:
{rag_context}

Rules:
- Always cite the specific DAG ID, run ID, task name, and try number when describing failures.
- Use source URIs in the format: airflow:dag={{dag_id}}/task={{task_id}}/run={{run_id}}/try={{try}}/ts={{ts}}
- If you cannot determine the cause with confidence, say so explicitly.
- End your response with a "Sources:" section listing each airflow: URI you used."""

async def run_workflow_agent(query: str) -> tuple[str, bool]:
    """Run the workflow sub-agent.

    Returns (response_text, rerank_fallback).
    rerank_fallback=True when Cohere was unavailable for RAG retrieval.
    """
    dag_id = _extract_dag_id(query)
    chunks, rerank_fallback = await retrieve(
        query,
        table="airflow_embeddings",
        filter_col="dag_id" if dag_id else None,
        filter_val=dag_id,
    )

    rag_context = "\n\n".join(
        f"[Source: {c['source_uri']}]\n{c['text']}" for c in chunks
    ) or "No relevant historical context found."

    llm = ChatOpenAI(
        model="gpt-4o", temperature=0, max_tokens=2048,
        openai_api_key=settings.openai_api_key,
    )
    tools = _workflow_tools
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ]).partial(rag_context=rag_context)

    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, max_iterations=5,
                             handle_parsing_errors=True)
    result = await executor.ainvoke({"input": query})
    return result["output"], rerank_fallback
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_workflow_agent.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 3: Commit**

```bash
git add backend/agents/workflow_agent.py backend/tests/test_workflow_agent.py
git commit -m "feat: workflow sub-agent (MCP tools + hybrid RAG, dag_id detection)"
```
