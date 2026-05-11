import signal

import airflow_client.client as airflow_client
from airflow_client.client.api import dag_api, dag_run_api, task_instance_api
from mcp.server.fastmcp import FastMCP

from config import settings

MAX_LOG_PAGES = settings.max_log_pages
mcp = FastMCP("airflow-tools")


def _dag_run_date(r) -> str | None:
    """Prefer logical_date (Airflow 3 canonical); fall back to execution_date."""
    return str(getattr(r, "logical_date", None) or getattr(r, "execution_date", None))


def _dag_schedule(dag) -> str | None:
    """Prefer schedule (Airflow 3); fall back to schedule_interval."""
    return getattr(dag, "schedule", None) or getattr(dag, "schedule_interval", None)


def _client():
    base_url = settings.airflow_base_url
    if base_url and not base_url.startswith(("http://", "https://")):
        base_url = f"https://{base_url}"
    cfg = airflow_client.Configuration(host=base_url)
    if settings.airflow_token:
        cfg.access_token = settings.airflow_token
    else:
        cfg.username = settings.airflow_username
        cfg.password = settings.airflow_password
    cfg.connection_pool_maxsize = 4
    return airflow_client.ApiClient(cfg)


@mcp.tool()
def ping() -> dict:
    """Zero-side-effect liveness probe — returns immediately without touching the Airflow API."""
    return {"ok": True}


@mcp.tool()
def get_dag_status(dag_id: str) -> dict:
    """Current DAG metadata and most recent run outcome."""
    with _client() as c:
        dag = dag_api.DAGApi(c).get_dag(dag_id)
        runs = dag_run_api.DagRunApi(c).get_dag_runs(
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
        runs = dag_run_api.DagRunApi(c).get_dag_runs(
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
    Call this before get_task_log when the user asks about the 'latest attempt'."""
    with _client() as c:
        api = task_instance_api.TaskInstanceApi(c)
        ti = api.get_task_instance(dag_id=dag_id, dag_run_id=dag_run_id, task_id=task_id)
        return getattr(ti, "try_number", 1) or 1


@mcp.tool()
def get_task_log(
    dag_id: str, dag_run_id: str, task_id: str, task_try_number: int = 1
) -> dict:
    """Paginated task-level logs. Handles Airflow 3 list[str] content.
    Returns {text, truncated, pages_returned}."""
    pages: list[str] = []
    token: str | None = None
    page_count = 0
    with _client() as c:
        api = task_instance_api.TaskInstanceApi(c)
        for _ in range(MAX_LOG_PAGES):
            resp = api.get_log(
                dag_id=dag_id,
                dag_run_id=dag_run_id,
                task_id=task_id,
                try_number=task_try_number,
                full_content=True,
                token=token,
            )
            content = resp.content
            if isinstance(content, list):
                parts = [
                    item if isinstance(item, str) else getattr(item, "text", str(item))
                    for item in content
                ]
                pages.append("\n".join(parts))
            else:
                pages.append(content or "")
            page_count += 1
            raw_token = getattr(resp, "next_token", None)
            token = raw_token if isinstance(raw_token, str) else None
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
    mcp.run()
