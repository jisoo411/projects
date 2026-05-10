import asyncio

from langchain.agents import create_agent as _create_agent
from langchain_openai import ChatOpenAI

from config import settings
from rag.retriever import retrieve
from tools.db_quality_tool import get_cached_metrics, get_quality_tools

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


async def run_quality_agent(query: str) -> tuple[str, bool]:
    """Run the data quality sub-agent. Returns (response_text, rerank_fallback).

    Reads live_metrics cache for general queries.
    Triggers on-demand live check (gated by semaphore) when a specific table is named.
    """
    table_name = _extract_table_name(query)

    cached_lines: list[str] = []
    for tbl in _MONITORED_TABLES:
        cached = await get_cached_metrics(tbl)
        if cached:
            cached_lines.append(
                f"- {tbl}: score={cached.get('value', '?')}, "
                f"status={cached.get('status', '?')}, "
                f"observed={cached.get('observed_at', '?')}"
            )
    cached_context = (
        "\n".join(cached_lines) or "No cache data yet (first ingestion cycle pending)."
    )

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
        model="gpt-4o",
        temperature=0,
        max_tokens=2048,
        openai_api_key=settings.openai_api_key,
        model_kwargs={"safety_identifier": "pipeline-observability-quality"},
    )
    system_msg = SYSTEM_PROMPT.format(cached_context=cached_context, rag_context=rag_context)
    graph = _create_agent(llm, tools, system_prompt=system_msg)
    executor = AgentExecutor(agent=graph, tools=tools, max_iterations=5, handle_parsing_errors=True)
    result = await executor.ainvoke({"input": query})
    return result["output"], rerank_fallback
