# Airflow MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a custom FastMCP stdio server wrapping the Airflow 3.x REST API. Fixes the Airflow 3 log content bug (returns `list[str]`, not `str`), exposes a side-effect-free `ping` tool for health checks, handles log pagination, and registers a SIGTERM handler for graceful shutdown.

**Architecture:** Runs as a long-lived stdio subprocess of the FastAPI process — no separate deployment. `langchain-mcp-adapters` auto-converts its tools to LangChain-compatible tools. All MCP tool calls are synchronous; the agent side wraps them with `asyncio.wait_for` + `run_in_executor` to enforce hard per-call deadlines.

**Tech Stack:** `mcp==1.3.0` (FastMCP), `apache-airflow-client==3.0.0`

---

## File Map

```
backend/
├── airflow_mcp/
│   ├── __init__.py      # empty package marker
│   └── server.py        # FastMCP stdio server
└── tests/
    └── test_airflow_tool.py
```

---

### Task 1: Write Failing Tests

**Files:**
- Create: `backend/tests/test_airflow_tool.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_airflow_tool.py
from unittest.mock import patch, MagicMock

def _mock_client_ctx(mock_api):
    """Helper: returns a mock context manager that yields mock_api via _client()."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx

# ── get_task_log tests ────────────────────────────────────────────────────────

def test_get_task_log_joins_list_content():
    """Airflow 3: content is list[str] — must be joined into a single string."""
    from airflow_mcp.server import get_task_log
    mock_resp = MagicMock()
    mock_resp.content = ["line 1\n", "line 2\n", "KeyError 'order_id'"]
    mock_resp.next_token = None
    mock_api = MagicMock()
    mock_api.get_log.return_value = mock_resp
    with patch("airflow_mcp.server._client", return_value=_mock_client_ctx(mock_api)), \
         patch("airflow_mcp.server.task_instance_api.TaskInstanceApi", return_value=mock_api):
        result = get_task_log("orders_pipeline", "run_001", "extract", 1)
    assert isinstance(result, dict)
    assert "KeyError" in result["text"]
    assert result["truncated"] is False
    assert result["pages_returned"] == 1

def test_get_task_log_passthrough_str_content():
    """Airflow 2 compat: content is str — returned unchanged."""
    from airflow_mcp.server import get_task_log
    mock_resp = MagicMock()
    mock_resp.content = "plain string log content"
    mock_resp.next_token = None
    mock_api = MagicMock()
    mock_api.get_log.return_value = mock_resp
    with patch("airflow_mcp.server._client", return_value=_mock_client_ctx(mock_api)), \
         patch("airflow_mcp.server.task_instance_api.TaskInstanceApi", return_value=mock_api):
        result = get_task_log("orders_pipeline", "run_001", "extract", 1)
    assert result["text"] == "plain string log content"
    assert result["truncated"] is False

def test_get_task_log_pagination_two_pages_not_truncated():
    """Two-page response: next_token on page 1, none on page 2 → truncated=False, pages_returned=2."""
    from airflow_mcp.server import get_task_log
    page1 = MagicMock(); page1.content = ["page 1 content"]; page1.next_token = "tok"
    page2 = MagicMock(); page2.content = ["page 2 content"]; page2.next_token = None
    mock_api = MagicMock()
    mock_api.get_log.side_effect = [page1, page2]
    with patch("airflow_mcp.server._client", return_value=_mock_client_ctx(mock_api)), \
         patch("airflow_mcp.server.task_instance_api.TaskInstanceApi", return_value=mock_api):
        result = get_task_log("orders_pipeline", "run_001", "extract", 1)
    assert result["truncated"] is False
    assert result["pages_returned"] == 2
    assert "page 1 content" in result["text"]
    assert "page 2 content" in result["text"]

def test_get_task_log_max_pages_truncated():
    """MAX_LOG_PAGES=1 override with always-returning next_token → truncated=True, pages_returned=1."""
    from airflow_mcp import server as srv
    original = srv.MAX_LOG_PAGES
    srv.MAX_LOG_PAGES = 1
    try:
        mock_resp = MagicMock()
        mock_resp.content = ["log line"]
        mock_resp.next_token = "always_has_more"
        mock_api = MagicMock()
        mock_api.get_log.return_value = mock_resp
        with patch("airflow_mcp.server._client", return_value=_mock_client_ctx(mock_api)), \
             patch("airflow_mcp.server.task_instance_api.TaskInstanceApi", return_value=mock_api):
            result = srv.get_task_log("dag", "run", "task", 1)
        assert result["truncated"] is True
        assert result["pages_returned"] == 1
    finally:
        srv.MAX_LOG_PAGES = original

# ── get_latest_task_try tests ─────────────────────────────────────────────────

def test_get_latest_task_try_returns_one_on_none():
    """try_number=None from API → function returns 1 (not None)."""
    from airflow_mcp.server import get_latest_task_try
    mock_ti = MagicMock(); mock_ti.try_number = None
    mock_api = MagicMock(); mock_api.get_task_instance.return_value = mock_ti
    with patch("airflow_mcp.server._client", return_value=_mock_client_ctx(mock_api)), \
         patch("airflow_mcp.server.task_instance_api.TaskInstanceApi", return_value=mock_api):
        assert get_latest_task_try("dag", "run", "task") == 1

def test_get_latest_task_try_returns_one_on_zero():
    """try_number=0 from API → function returns 1."""
    from airflow_mcp.server import get_latest_task_try
    mock_ti = MagicMock(); mock_ti.try_number = 0
    mock_api = MagicMock(); mock_api.get_task_instance.return_value = mock_ti
    with patch("airflow_mcp.server._client", return_value=_mock_client_ctx(mock_api)), \
         patch("airflow_mcp.server.task_instance_api.TaskInstanceApi", return_value=mock_api):
        assert get_latest_task_try("dag", "run", "task") == 1

def test_get_latest_task_try_returns_actual_value():
    """try_number=3 from API → function returns 3."""
    from airflow_mcp.server import get_latest_task_try
    mock_ti = MagicMock(); mock_ti.try_number = 3
    mock_api = MagicMock(); mock_api.get_task_instance.return_value = mock_ti
    with patch("airflow_mcp.server._client", return_value=_mock_client_ctx(mock_api)), \
         patch("airflow_mcp.server.task_instance_api.TaskInstanceApi", return_value=mock_api):
        assert get_latest_task_try("dag", "run", "task") == 3

# ── ping test ─────────────────────────────────────────────────────────────────

def test_ping_returns_ok_without_airflow_call():
    """ping() must return {"ok": True} without touching the Airflow API."""
    from airflow_mcp.server import ping
    with patch("airflow_mcp.server._client") as mock_client:
        result = ping()
    assert result == {"ok": True}
    mock_client.assert_not_called()

# ── source URI format test ────────────────────────────────────────────────────

def test_source_uri_format():
    uri = "airflow:dag=orders_pipeline/task=extract/run=run_001/try=2/ts=2026-04-22T02:14:00Z"
    assert uri.startswith("airflow:dag=")
    assert "/task=" in uri
    assert "/run=" in uri
    assert "/try=" in uri
    assert "/ts=" in uri
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_airflow_tool.py -v
```

Expected: FAIL — `airflow_mcp.server` not found.

---

### Task 2: Implement the MCP Server

**Files:**
- Create: `backend/airflow_mcp/server.py`

- [ ] **Step 1: Write `backend/airflow_mcp/server.py`**

```python
import signal
from mcp.server.fastmcp import FastMCP
import apache_airflow_client
from apache_airflow_client.api import dag_api, dag_run_api, task_instance_api
from config import settings

MAX_LOG_PAGES = settings.max_log_pages
mcp = FastMCP("airflow-tools")

def _dag_run_date(r) -> str | None:
    """Prefer logical_date (Airflow 3 canonical); fall back to execution_date (deprecated)."""
    return str(getattr(r, "logical_date", None) or getattr(r, "execution_date", None))

def _dag_schedule(dag) -> str | None:
    """Prefer schedule (Airflow 3); fall back to schedule_interval."""
    return getattr(dag, "schedule", None) or getattr(dag, "schedule_interval", None)

def _client():
    cfg = apache_airflow_client.Configuration(host=settings.airflow_base_url)
    if settings.airflow_token:
        cfg.access_token = settings.airflow_token
    else:
        cfg.username = settings.airflow_username
        cfg.password = settings.airflow_password
    # Timeouts are NOT set here — they are not reliably honoured across all transports.
    # Hard deadlines are enforced on the caller side via asyncio.wait_for + run_in_executor.
    cfg.connection_pool_maxsize = 4
    return apache_airflow_client.ApiClient(cfg)

@mcp.tool()
def ping() -> dict:
    """Zero-side-effect liveness probe — returns immediately, never touches the Airflow API.
    Called by the /health watchdog every 30 s."""
    return {"ok": True}

@mcp.tool()
def get_dag_status(dag_id: str) -> dict:
    """Current DAG metadata and most recent run outcome."""
    with _client() as c:
        dag = dag_api.DAGApi(c).get_dag(dag_id)
        runs = dag_run_api.DAGRunApi(c).get_dag_runs(
            dag_id, limit=1, order_by="-logical_date"
        )
        last = runs.dag_runs[0] if runs.dag_runs else None
        return {
            "dag_id": dag.dag_id,
            "is_paused": dag.is_paused,
            "schedule": _dag_schedule(dag),
            "last_run_id": last.dag_run_id if last else None,
            "last_run_state": last.state if last else None,
            "last_run_date": _dag_run_date(last) if last else None,
            "last_run_end_date": str(last.end_date) if last else None,
        }

@mcp.tool()
def get_latest_dag_runs(dag_id: str, limit: int = 10) -> list[dict]:
    """Recent DAG run history with states and timing."""
    with _client() as c:
        runs = dag_run_api.DAGRunApi(c).get_dag_runs(
            dag_id, limit=limit, order_by="-logical_date"
        )
        return [
            {
                "dag_run_id": r.dag_run_id,
                "state": r.state,
                "logical_date": _dag_run_date(r),
                "start_date": str(r.start_date),
                "end_date": str(r.end_date),
            }
            for r in runs.dag_runs
        ]

@mcp.tool()
def get_latest_task_try(dag_id: str, dag_run_id: str, task_id: str) -> int:
    """Returns the highest try_number for this task instance.
    Call this before get_task_log when the user asks about the 'latest attempt'
    to avoid defaulting to try=1 when 2+ retries exist."""
    with _client() as c:
        api = task_instance_api.TaskInstanceApi(c)
        ti = api.get_task_instance(dag_id=dag_id, dag_run_id=dag_run_id, task_id=task_id)
        return getattr(ti, "try_number", 1) or 1

@mcp.tool()
def get_task_log(
    dag_id: str, dag_run_id: str, task_id: str, task_try_number: int = 1
) -> dict:
    """Paginated task-level logs. Handles Airflow 3 list[str] content (community MCP issue #50).
    Returns {text, truncated, pages_returned}. Surface 'truncated' to the user with a source link."""
    pages: list[str] = []
    token: str | None = None
    page_count = 0
    with _client() as c:
        api = task_instance_api.TaskInstanceApi(c)
        for _ in range(MAX_LOG_PAGES):
            resp = api.get_log(
                dag_id=dag_id, dag_run_id=dag_run_id, task_id=task_id,
                task_try_number=task_try_number, full_content=True, token=token,
            )
            content = resp.content
            # Airflow 3: content is list[str]. Airflow 2 compat: content is str.
            pages.append("\n".join(content) if isinstance(content, list) else (content or ""))
            page_count += 1
            token = getattr(resp, "next_token", None) or getattr(resp, "continuation_token", None)
            if not token:
                break
    return {
        "text": "\n".join(pages),
        "truncated": token is not None,
        "pages_returned": page_count,
    }

def _handle_sigterm(signum, frame):
    raise SystemExit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_sigterm)
    mcp.run()   # starts the stdio transport; required — without this the process exits immediately
```

- [ ] **Step 2: Run tests to verify they all pass**

```bash
cd backend && python -m pytest tests/test_airflow_tool.py -v
```

Expected: PASS (9 tests)

- [ ] **Step 3: Commit**

```bash
git add backend/airflow_mcp/ backend/tests/test_airflow_tool.py
git commit -m "feat: custom Airflow MCP server (FastMCP, Airflow 3 list fix, pagination, SIGTERM)"
```
