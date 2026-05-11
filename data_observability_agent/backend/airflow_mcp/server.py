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

    auth_method = "token" if settings.airflow_token else "basic_auth"
    token_preview = (
        f"{settings.airflow_token[:12]}...{settings.airflow_token[-6:]}"
        if settings.airflow_token else "NOT SET"
    )
    print(
        f"[airflow_mcp] _client() — base_url={base_url!r} "
        f"auth={auth_method} token={token_preview} "
        f"username={settings.airflow_username!r}",
        flush=True,
    )

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
    print(f"[airflow_mcp] get_dag_status(dag_id={dag_id!r})", flush=True)
    try:
        with _client() as c:
            dag = dag_api.DAGApi(c).get_dag(dag_id)
            print(f"[airflow_mcp] get_dag — response: {dag}", flush=True)
            runs = dag_run_api.DagRunApi(c).get_dag_runs(
                dag_id, limit=1, order_by="-logical_date"
            )
            print(f"[airflow_mcp] get_dag_runs — response: {runs}", flush=True)
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
    except Exception as exc:
        print(f"[airflow_mcp] get_dag_status ERROR: {type(exc).__name__}: {exc}", flush=True)
        raise


@mcp.tool()
def get_latest_dag_runs(dag_id: str, limit: int = 10) -> list[dict]:
    """Recent DAG run history with states and timing."""
    print(f"[airflow_mcp] get_latest_dag_runs(dag_id={dag_id!r}, limit={limit})", flush=True)
    try:
        with _client() as c:
            runs = dag_run_api.DagRunApi(c).get_dag_runs(
                dag_id, limit=limit, order_by="-logical_date"
            )
            print(f"[airflow_mcp] get_dag_runs — response: {runs}", flush=True)
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
    except Exception as exc:
        print(f"[airflow_mcp] get_latest_dag_runs ERROR: {type(exc).__name__}: {exc}", flush=True)
        raise


@mcp.tool()
def get_latest_task_try(dag_id: str, dag_run_id: str, task_id: str) -> int:
    """Returns the highest try_number for this task instance.
    Call this before get_task_log when the user asks about the 'latest attempt'."""
    print(
        f"[airflow_mcp] get_latest_task_try(dag_id={dag_id!r}, "
        f"dag_run_id={dag_run_id!r}, task_id={task_id!r})",
        flush=True,
    )
    try:
        with _client() as c:
            api = task_instance_api.TaskInstanceApi(c)
            ti = api.get_task_instance(dag_id=dag_id, dag_run_id=dag_run_id, task_id=task_id)
            print(f"[airflow_mcp] get_task_instance — response: {ti}", flush=True)
            return getattr(ti, "try_number", 1) or 1
    except Exception as exc:
        print(f"[airflow_mcp] get_latest_task_try ERROR: {type(exc).__name__}: {exc}", flush=True)
        raise


@mcp.tool()
def get_task_log(
    dag_id: str, dag_run_id: str, task_id: str, task_try_number: int = 1
) -> dict:
    """Paginated task-level logs. Handles Airflow 3 list[str] content.
    Returns {text, truncated, pages_returned}."""
    print(
        f"[airflow_mcp] get_task_log(dag_id={dag_id!r}, dag_run_id={dag_run_id!r}, "
        f"task_id={task_id!r}, try={task_try_number})",
        flush=True,
    )
    pages: list[str] = []
    token: str | None = None
    page_count = 0
    try:
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
                print(
                    f"[airflow_mcp] get_log page {page_count + 1} — "
                    f"content_type={type(resp.content).__name__} "
                    f"next_token={resp.next_token!r}",
                    flush=True,
                )
                content = resp.content
                pages.append("\n".join(content) if isinstance(content, list) else (content or ""))
                page_count += 1
                token = resp.next_token if isinstance(resp.next_token, str) else None
                if not token:
                    break
    except Exception as exc:
        print(f"[airflow_mcp] get_task_log ERROR: {type(exc).__name__}: {exc}", flush=True)
        raise
    return {
        "text": "\n".join(pages),
        "truncated": token is not None,
        "pages_returned": page_count,
    }


def _handle_sigterm(signum, frame):
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_sigterm)
    print(
        f"[airflow_mcp] starting — airflow_base_url={settings.airflow_base_url!r} "
        f"token_set={bool(settings.airflow_token)} "
        f"username={settings.airflow_username!r}",
        flush=True,
    )
    mcp.run()
