from unittest.mock import patch, MagicMock


def _mock_client_ctx(mock_api):
    """Returns a mock context manager; __enter__ returns itself."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ── get_task_log tests ─────────────────────────────────────────────────────────

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
    """MAX_LOG_PAGES=1 with always-returning next_token → truncated=True, pages_returned=1."""
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


# ── get_latest_task_try tests ──────────────────────────────────────────────────

def test_get_latest_task_try_returns_one_on_none():
    """try_number=None from API → function returns 1."""
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


# ── ping test ──────────────────────────────────────────────────────────────────

def test_ping_returns_ok_without_airflow_call():
    """ping() must return {"ok": True} without touching the Airflow API."""
    from airflow_mcp.server import ping
    with patch("airflow_mcp.server._client") as mock_client:
        result = ping()
    assert result == {"ok": True}
    mock_client.assert_not_called()


# ── source URI format test ─────────────────────────────────────────────────────

def test_source_uri_format():
    uri = "airflow:dag=orders_pipeline/task=extract/run=run_001/try=2/ts=2026-04-22T02:14:00Z"
    assert uri.startswith("airflow:dag=")
    assert "/task=" in uri
    assert "/run=" in uri
    assert "/try=" in uri
    assert "/ts=" in uri
