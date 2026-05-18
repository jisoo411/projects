# LangGraph Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the LangGraph orchestrator that fans out queries to the Workflow and Quality sub-agents in parallel, then synthesizes a unified response with GPT-4o. Returns `(response_text, rerank_fallback)`.

**Architecture:** Uses a `StateGraph` with two nodes: `fan_out` (parallel asyncio dispatch with 30s timeout) and `synthesis` (GPT-4o prompt combining both sub-agent outputs). `OrchestratorState` carries query, per-agent results, and the rerank fallback flag. The compiled graph is invoked via `run_orchestrator(query)`.

**Tech Stack:** LangGraph, langchain-openai, OpenAI GPT-4o, asyncio

---

## File Map

```
backend/
├── agents/
│   └── orchestrator.py
└── tests/
    └── test_orchestrator.py
```

---

### Task 1: Write Failing Tests

**Files:**
- Create: `backend/tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_orchestrator.py
import pytest
from unittest.mock import patch, AsyncMock

WORKFLOW_OUT = (
    "orders_pipeline failed at extract_orders — KeyError 'order_id'. "
    "Sources: airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z",
    False,
)
QUALITY_OUT = (
    "orders table quality score is 0.58 (warn). "
    "Sources: dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z",
    False,
)

@pytest.mark.asyncio
async def test_orchestrator_returns_text_and_fallback_flag():
    from agents.orchestrator import run_orchestrator
    with patch("agents.orchestrator.run_workflow_agent", new=AsyncMock(return_value=WORKFLOW_OUT)), \
         patch("agents.orchestrator.run_quality_agent", new=AsyncMock(return_value=QUALITY_OUT)), \
         patch("agents.orchestrator.ChatOpenAI") as mock_llm_cls:
        mock_llm = mock_llm_cls.return_value
        mock_llm.ainvoke = AsyncMock(return_value=type("R", (), {
            "content": "The orders pipeline failed due to a null key. Quality score is 0.58. "
                       "Sources: airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z, "
                       "dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z"
        })())
        text, fallback = await run_orchestrator("Why did orders_pipeline fail?")
    assert isinstance(text, str) and len(text) > 0
    assert fallback is False

@pytest.mark.asyncio
async def test_orchestrator_propagates_rerank_fallback():
    """If either sub-agent signals rerank_fallback=True, orchestrator propagates it."""
    from agents.orchestrator import run_orchestrator
    workflow_with_fallback = (WORKFLOW_OUT[0], True)
    with patch("agents.orchestrator.run_workflow_agent",
               new=AsyncMock(return_value=workflow_with_fallback)), \
         patch("agents.orchestrator.run_quality_agent", new=AsyncMock(return_value=QUALITY_OUT)), \
         patch("agents.orchestrator.ChatOpenAI") as mock_llm_cls:
        mock_llm = mock_llm_cls.return_value
        mock_llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": "result"})())
        _, fallback = await run_orchestrator("pipeline status?")
    assert fallback is True

@pytest.mark.asyncio
async def test_orchestrator_handles_workflow_agent_timeout():
    """If workflow agent times out, orchestrator still completes using quality output."""
    import asyncio
    from agents.orchestrator import run_orchestrator

    async def slow_workflow(_query):
        await asyncio.sleep(999)
        return ("never", False)

    with patch("agents.orchestrator.run_workflow_agent", new=slow_workflow), \
         patch("agents.orchestrator.run_quality_agent", new=AsyncMock(return_value=QUALITY_OUT)), \
         patch("agents.orchestrator.AGENT_TIMEOUT_S", 0.05), \
         patch("agents.orchestrator.ChatOpenAI") as mock_llm_cls:
        mock_llm = mock_llm_cls.return_value
        mock_llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": "partial result"})())
        text, _ = await run_orchestrator("data quality status?")
    assert isinstance(text, str) and len(text) > 0

@pytest.mark.asyncio
async def test_orchestrator_state_carries_both_agent_outputs():
    """Both sub-agent outputs are passed to the synthesis prompt."""
    from agents.orchestrator import run_orchestrator
    captured_prompt = {}

    async def fake_ainvoke(messages, **_kwargs):
        captured_prompt["messages"] = messages
        return type("R", (), {"content": "synthesis"})()

    with patch("agents.orchestrator.run_workflow_agent", new=AsyncMock(return_value=WORKFLOW_OUT)), \
         patch("agents.orchestrator.run_quality_agent", new=AsyncMock(return_value=QUALITY_OUT)), \
         patch("agents.orchestrator.ChatOpenAI") as mock_llm_cls:
        mock_llm = mock_llm_cls.return_value
        mock_llm.ainvoke = AsyncMock(side_effect=fake_ainvoke)
        await run_orchestrator("pipeline and quality summary?")

    # Synthesis prompt must include content from both agents
    prompt_str = str(captured_prompt.get("messages", ""))
    assert "orders_pipeline" in prompt_str
    assert "quality score" in prompt_str
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_orchestrator.py -v
```

Expected: FAIL — `agents.orchestrator` not found.

---

### Task 2: Implement the Orchestrator

**Files:**
- Create: `backend/agents/orchestrator.py`

- [ ] **Step 1: Write `backend/agents/orchestrator.py`**

```python
import asyncio
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from config import settings
from agents.workflow_agent import run_workflow_agent
from agents.quality_agent import run_quality_agent

AGENT_TIMEOUT_S: float = 30.0

class OrchestratorState(TypedDict):
    query: str
    workflow_result: str
    workflow_fallback: bool
    quality_result: str
    quality_fallback: bool
    response: str
    rerank_fallback: bool

async def _fan_out_node(state: OrchestratorState) -> OrchestratorState:
    """Dispatch both sub-agents in parallel; cancel and return partial on timeout."""
    query = state["query"]
    wf_task = asyncio.create_task(run_workflow_agent(query))
    qa_task = asyncio.create_task(run_quality_agent(query))

    done, pending = await asyncio.wait(
        {wf_task, qa_task},
        timeout=AGENT_TIMEOUT_S,
    )
    for t in pending:
        t.cancel()

    wf_text, wf_fallback = wf_task.result() if wf_task in done else ("(workflow agent timed out)", False)
    qa_text, qa_fallback = qa_task.result() if qa_task in done else ("(quality agent timed out)", False)

    return {
        **state,
        "workflow_result": wf_text,
        "workflow_fallback": wf_fallback,
        "quality_result": qa_text,
        "quality_fallback": qa_fallback,
        "rerank_fallback": wf_fallback or qa_fallback,
    }

SYNTHESIS_SYSTEM = """You are a pipeline observability assistant synthesizing reports from two specialist agents.

Workflow agent report:
{workflow_result}

Data quality agent report:
{quality_result}

Instructions:
- Combine both reports into a single coherent answer.
- Do not repeat information; merge overlapping findings.
- Preserve ALL source URIs from both reports.
- End with a "Sources:" section listing every airflow: and dbcheck: URI cited."""

async def _synthesis_node(state: OrchestratorState) -> OrchestratorState:
    llm = ChatOpenAI(
        model="gpt-4o", temperature=0, max_tokens=2048,
        openai_api_key=settings.openai_api_key,
    )
    system_content = SYNTHESIS_SYSTEM.format(
        workflow_result=state["workflow_result"],
        quality_result=state["quality_result"],
    )
    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=state["query"]),
    ]
    result = await llm.ainvoke(messages)
    return {**state, "response": result.content}

def _build_graph() -> StateGraph:
    graph = StateGraph(OrchestratorState)
    graph.add_node("fan_out", _fan_out_node)
    graph.add_node("synthesis", _synthesis_node)
    graph.set_entry_point("fan_out")
    graph.add_edge("fan_out", "synthesis")
    graph.add_edge("synthesis", END)
    return graph.compile()

_graph = _build_graph()

async def run_orchestrator(query: str) -> tuple[str, bool]:
    """Run the LangGraph orchestrator.

    Returns (response_text, rerank_fallback).
    rerank_fallback=True when either sub-agent used vector-only retrieval.
    """
    initial_state: OrchestratorState = {
        "query": query,
        "workflow_result": "",
        "workflow_fallback": False,
        "quality_result": "",
        "quality_fallback": False,
        "response": "",
        "rerank_fallback": False,
    }
    final_state = await _graph.ainvoke(initial_state)
    return final_state["response"], final_state["rerank_fallback"]
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_orchestrator.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 3: Commit**

```bash
git add backend/agents/orchestrator.py backend/tests/test_orchestrator.py
git commit -m "feat: LangGraph orchestrator (fan_out + synthesis, 30s timeout, rerank_fallback propagation)"
```
