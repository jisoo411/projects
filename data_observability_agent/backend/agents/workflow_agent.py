import re

from langchain.agents import create_agent as _create_react_agent
from langchain_openai import ChatOpenAI

from config import settings
from rag.retriever import retrieve

_workflow_tools: list = []


def set_workflow_tools(tools: list) -> None:
    global _workflow_tools
    _workflow_tools = tools


_KNOWN_DAG_IDS = ["nasa_neo_ingest", "nasa_apod_ingest"]


def _extract_dag_id(query: str) -> str | None:
    """Checks known DAG IDs first, then falls back to regex for snake_case DAG-like tokens."""
    q_lower = query.lower()
    for dag_id in _KNOWN_DAG_IDS:
        if dag_id.lower() in q_lower:
            return dag_id
    match = re.search(
        r"\b([a-z][a-z0-9_]+_(?:dag|pipeline|flow|job|sync|load|agg|ingest))\b", q_lower
    )
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


class AgentExecutor:
    """Thin LangGraph wrapper that preserves the AgentExecutor.ainvoke interface."""

    def __init__(self, agent=None, tools=None, max_iterations=5, handle_parsing_errors=True, **_):
        self._graph = agent

    async def ainvoke(self, inputs: dict) -> dict:
        result = await self._graph.ainvoke(
            {"messages": [("human", inputs.get("input", ""))]}
        )
        messages = result.get("messages", [])
        output = messages[-1].content if messages else ""
        return {"output": output}


async def run_workflow_agent(query: str) -> tuple[str, bool]:
    """Run the workflow sub-agent. Returns (response_text, rerank_fallback)."""
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
        model="gpt-4o",
        temperature=0,
        max_tokens=2048,
        openai_api_key=settings.openai_api_key,
        model_kwargs={"user": "pipeline-observability-workflow"},
    )
    tools = _workflow_tools
    system_msg = SYSTEM_PROMPT.format(rag_context=rag_context)
    graph = _create_react_agent(llm, tools, system_prompt=system_msg)
    executor = AgentExecutor(agent=graph, tools=tools, max_iterations=5, handle_parsing_errors=True)
    result = await executor.ainvoke({"input": query})
    return result["output"], rerank_fallback
