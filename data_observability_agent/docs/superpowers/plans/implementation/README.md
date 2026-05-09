# Pipeline Observability Agent — Build Sequence & Dependency Map

Each subfolder contains a self-contained implementation plan. Build in the order below.
Later plans import from earlier ones — do not skip ahead.

---

## Build Order

| # | Plan | Depends On | Produces |
|---|---|---|---|
| 1 | [01-foundation/01-repo-scaffold](01-foundation/01-repo-scaffold.md) | nothing | `config.py`, `models.py`, `requirements.txt`, `.env.example`, `config/thresholds.yaml` |
| 2 | [01-foundation/02-database-schema](01-foundation/02-database-schema.md) | #1 (DB URL in config) | 6 PostgreSQL tables + all indexes |
| 3 | [02-rag/01-rag-layer](02-rag/01-rag-layer.md) | #1, #2 | `rag/embedder.py`, `rag/retriever.py` |
| 4 | [03-tools/01-airflow-mcp-server](03-tools/01-airflow-mcp-server.md) | #1 | `airflow_mcp/server.py` |
| 5 | [03-tools/02-db-quality-tool](03-tools/02-db-quality-tool.md) | #1, #2, #3 | `tools/db_quality_tool.py` |
| 6 | [04-agents/01-workflow-agent](04-agents/01-workflow-agent.md) | #1, #3, #4 | `agents/workflow_agent.py` |
| 7 | [04-agents/02-quality-agent](04-agents/02-quality-agent.md) | #1, #3, #5 | `agents/quality_agent.py` |
| 8 | [04-agents/03-orchestrator](04-agents/03-orchestrator.md) | #6, #7 | `agents/orchestrator.py` |
| 9 | [05-guardrails/01-input-guardrails](05-guardrails/01-input-guardrails.md) | #1 | `guardrails/input_guardrails.py` |
| 10 | [05-guardrails/02-output-guardrails](05-guardrails/02-output-guardrails.md) | #1 | `guardrails/output_guardrails.py` |
| 11 | [06-api/01-fastapi-main](06-api/01-fastapi-main.md) | #4, #6, #8 | `main.py` + MCP watchdog |
| 12 | [06-api/02-status-health-endpoints](06-api/02-status-health-endpoints.md) | #5, #11 | `api/health.py`, `api/status.py` |
| 13 | [06-api/03-chat-sse-endpoint](06-api/03-chat-sse-endpoint.md) | #8, #9, #10, #11 | `api/chat.py` |
| 14 | [07-ingestion/01-ingestion-pipeline](07-ingestion/01-ingestion-pipeline.md) | #3, #4, #5 | `ingestion/airflow_ingestor.py`, `ingestion/quality_ingestor.py` |
| 15 | [08-capstone-tests/01-capstone-tests](08-capstone-tests/01-capstone-tests.md) | #8, #9, #10, #13 | `tests/test_capstone.py` |
| 16 | [09-frontend/01-frontend](09-frontend/01-frontend.md) | #11, #12, #13 | Next.js components + source URI resolver |
| 17 | [10-deployment/01-deployment](10-deployment/01-deployment.md) | all | Render + Vercel live deployment |

---

## Dependency Graph

```
#1 (scaffold)
 └─ #2 (schema)
     └─ #3 (RAG layer)
         ├─ #5 (DB quality tool)
         │   ├─ #7 (quality agent)
         │   │   └─ #8 (orchestrator)
         │   │       ├─ #13 (chat SSE)
         │   │       └─ #15 (capstone tests)
         │   └─ #12 (status/health)
         └─ #6 (workflow agent) ─┐
#4 (Airflow MCP)                 └─ #8 (orchestrator)
 └─ #6 (workflow agent)
 └─ #11 (FastAPI main) ─ #12, #13
#9 (input guards) ─ #13
#10 (output guards) ─ #13
#14 (ingestion) ─ depends on #3, #4, #5 (independent from #6–#13)
#16 (frontend) ─ depends on #11–#13
#17 (deployment) ─ depends on everything
```

---

## Key Shared Interfaces

These signatures are used across multiple plans — get them right in the defining plan and never change them.

| Symbol | Defined In | Signature |
|---|---|---|
| `embed` | `rag/embedder.py` | `async embed(text: str) -> list[float]` |
| `retrieve` | `rag/retriever.py` | `async retrieve(query, table, filter_col=None, filter_val=None, top_k=8, top_n=3) -> tuple[list[dict], bool]` |
| `get_pool` | `rag/retriever.py` | `async get_pool() -> asyncpg.Pool` — pipeline_watch DB (embeddings, metrics, audit) |
| `get_source_pool` | `rag/retriever.py` | `async get_source_pool() -> asyncpg.Pool` — source data DB (orders, users, inventory, airflow_task_logs) |
| `run_workflow_agent` | `agents/workflow_agent.py` | `async run_workflow_agent(query: str) -> tuple[str, bool]` |
| `run_quality_agent` | `agents/quality_agent.py` | `async run_quality_agent(query: str) -> tuple[str, bool]` |
| `run_orchestrator` | `agents/orchestrator.py` | `async run_orchestrator(query: str) -> tuple[str, bool]` |
| `set_workflow_tools` | `agents/workflow_agent.py` | `set_workflow_tools(tools: list) -> None` |
| `run_input_guards` | `guardrails/input_guards.py` | `run_input_guards(query: str) -> str` (raises `InputGuardError`) |
| `run_output_guards` | `guardrails/output_guards.py` | `run_output_guards(response: str, context: str) -> str` (raises `OutputGuardError`) |
| `ChatResponse` | `models.py` | Pydantic model — summary, current_state, root_causes, recommended_actions, citations, confidence, degraded_reranking |

---

## Source URI Format

All citations must use structured URIs — checked in capstone tests.

- Airflow: `airflow:dag={dag_id}/task={task_id}/run={run_id}/try={try_number}/ts={timestamp}`
- Data quality: `dbcheck:table={table_name}/metric={metric_type}/ts={timestamp}`

---

## Environment Variables

All required and optional vars are defined in `backend/.env.example` (Task 1).
Before running any plan's tests, copy `.env.example` to `.env` and fill in real values,
or ensure the test suite mocks all external calls (all plans use mocks — no live keys needed for unit tests).

`DATABASE_URL` and `SOURCE_DATABASE_URL` both point to logical databases within the same Neon project
(different `dbname`, same host). `pipeline_watch` holds embeddings, metrics, and audit tables.
`source_data` holds the monitored business tables (`orders`, `users`, `inventory_items`, `revenue_aggregate`,
`airflow_task_logs`). The Airflow metadata database stays on Render Postgres (co-located with Airflow).
