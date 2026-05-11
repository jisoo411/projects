import asyncio
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from agents.quality_agent import run_quality_agent
from agents.workflow_agent import run_workflow_agent
from config import settings

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

    done, pending = await asyncio.wait({wf_task, qa_task}, timeout=AGENT_TIMEOUT_S)
    for t in pending:
        t.cancel()

    def _safe(task: asyncio.Task, label: str) -> tuple[str, bool]:
        if task not in done:
            return (f"({label} timed out)", True)
        try:
            return task.result()
        except Exception as exc:
            return (f"({label} unavailable: {exc})", True)

    wf_text, wf_fallback = _safe(wf_task, "workflow agent")
    qa_text, qa_fallback = _safe(qa_task, "quality agent")

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
- If a report contains an error or unavailability message (e.g. "timed out", "unavailable", "No host"), \
acknowledge that the data source is temporarily unavailable rather than incorporating error text as factual data.
- Combine the available reports into a single coherent answer.
- Do not repeat information; merge overlapping findings.
- Write in plain prose sentences. Do NOT use markdown formatting, bullet points, numbered lists, or bold text.
- Preserve ALL source URIs from both reports.
- End with a "Sources:" section listing every airflow: and dbcheck: URI cited."""


async def _synthesis_node(state: OrchestratorState) -> OrchestratorState:
    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0,
        max_tokens=2048,
        openai_api_key=settings.openai_api_key,
        model_kwargs={"user": "pipeline-observability-orchestrator"},
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


def _build_graph():
    graph = StateGraph(OrchestratorState)
    graph.add_node("fan_out", _fan_out_node)
    graph.add_node("synthesis", _synthesis_node)
    graph.set_entry_point("fan_out")
    graph.add_edge("fan_out", "synthesis")
    graph.add_edge("synthesis", END)
    return graph.compile()


_graph = _build_graph()


async def run_orchestrator(query: str) -> tuple[str, bool, str]:
    """Fan out to both sub-agents, synthesize with GPT-4o.

    Returns (response_text, rerank_fallback, agent_context) where agent_context
    is the combined workflow + quality results passed to the hallucination checker.
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
    agent_context = (
        f"{final_state['workflow_result']}\n\n{final_state['quality_result']}"
    )
    return final_state["response"], final_state["rerank_fallback"], agent_context
