import pytest
from guardrails.output_guardrails import check_output, OutputGuardrailResult

VALID_CONTEXT = (
    "orders_pipeline failed at extract_orders. "
    "dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z score=0.58"
)


def test_valid_response_passes():
    response = (
        "The orders_pipeline failed due to a null key. "
        "Sources: airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z"
    )
    result = check_output(response, context=VALID_CONTEXT)
    assert result.blocked is False
    assert result.response == response


def test_response_without_sources_blocked():
    response = "The pipeline looks fine."
    result = check_output(response, context=VALID_CONTEXT)
    assert result.blocked is True
    assert result.block_reason == "missing_citations"


def test_response_with_invalid_uri_format_blocked():
    """'Sources:' present but URIs don't match airflow: or dbcheck: format."""
    response = "Pipeline is fine. Sources: https://example.com/logs"
    result = check_output(response, context=VALID_CONTEXT)
    assert result.blocked is True
    assert result.block_reason == "missing_citations"


def test_hallucinated_dag_blocked():
    """Response mentions a DAG ID not present in the context."""
    response = (
        "The fake_pipeline failed yesterday. "
        "Sources: airflow:dag=fake_pipeline/task=run/run=r1/try=1/ts=2026-04-22T02:00:00Z"
    )
    result = check_output(response, context=VALID_CONTEXT)
    assert result.blocked is True
    assert result.block_reason == "hallucination"


def test_hallucinated_table_blocked():
    """Response cites a table not in context."""
    response = (
        "The ghost_table has high null rate. "
        "Sources: dbcheck:table=ghost_table/metric=null_rate/ts=2026-04-22T01:00:00Z"
    )
    result = check_output(response, context=VALID_CONTEXT)
    assert result.blocked is True
    assert result.block_reason == "hallucination"


def test_valid_dbcheck_uri_passes():
    response = (
        "orders table quality score is 0.58. "
        "Sources: dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z"
    )
    result = check_output(response, context=VALID_CONTEXT)
    assert result.blocked is False
