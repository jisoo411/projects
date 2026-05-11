import logging
import re

from langchain.agents import create_agent as _create_react_agent
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

from config import settings
from rag.retriever import get_source_pool, retrieve

_workflow_tools: list = []


def set_workflow_tools(tools: list) -> None:
    global _workflow_tools
    _workflow_tools = tools


_KNOWN_DAG_IDS = ["nasa_neo_ingest", "nasa_apod_ingest"]


async def _resolve_dag_id_from_table(table_name: str) -> str | None:
    """Look up which DAG wrote to the given table via airflow_task_logs.destination_table."""
    try:
        pool = await get_source_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT dag_id FROM airflow_task_logs
                WHERE destination_table = $1
                ORDER BY logical_date DESC
                LIMIT 1
                """,
                table_name,
            )
        return row["dag_id"] if row else None
    except Exception:
        logger.exception("destination_table lookup failed for %r", table_name)
        return None


async def _extract_dag_id(query: str) -> str | None:
    """Checks known DAG IDs first, then table-to-DAG mapping, then regex fallback."""
    q_lower = query.lower()
    for dag_id in _KNOWN_DAG_IDS:
        if dag_id.lower() in q_lower:
            return dag_id

    # Extract candidate table names (snake_case tokens with at least one underscore)
    table_candidates = re.findall(r"\b([a-z][a-z0-9]+(?:_[a-z0-9]+)+)\b", q_lower)
    for candidate in table_candidates:
        dag_id = await _resolve_dag_id_from_table(candidate)
        if dag_id:
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
    try:
        return await _run_workflow_agent_inner(query)
    except Exception:
        logger.exception("workflow agent failed for query %r", query)
        raise


async def _run_workflow_agent_inner(query: str) -> tuple[str, bool]:
    dag_id = await _extract_dag_id(query)
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
