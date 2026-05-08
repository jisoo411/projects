import pytest
from models import Citation, ChatResponse, DegradedPayload, ChatRequest


def test_citation_truncates_excerpt_at_500():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Citation(
            source_id="airflow:dag=x/task=y/run=z/try=1/ts=2026-01-01T00:00:00Z",
            excerpt="x" * 501,
        )


def test_citation_accepts_max_length_excerpt():
    c = Citation(
        source_id="airflow:dag=x/task=y/run=z/try=1/ts=2026-01-01T00:00:00Z",
        excerpt="x" * 500,
    )
    assert len(c.excerpt) == 500


def test_chat_response_defaults():
    r = ChatResponse(
        summary="ok",
        current_state="healthy",
        root_causes=[],
        recommended_actions=[],
        citations=[
            Citation(
                source_id="airflow:dag=orders/task=extract/run=r1/try=1/ts=2026-01-01T00:00:00Z",
                excerpt="task failed",
            )
        ],
    )
    assert r.confidence == 0.0
    assert r.degraded_reranking is False


def test_chat_response_degraded_flag():
    r = ChatResponse(
        summary="partial",
        current_state="degraded",
        root_causes=["Cohere unavailable"],
        recommended_actions=[],
        citations=[
            Citation(
                source_id="dbcheck:table=orders/metric=composite/ts=2026-01-01T00:00:00Z",
                excerpt="score 0.58",
            )
        ],
        degraded_reranking=True,
        confidence=0.52,
    )
    assert r.degraded_reranking is True
    assert r.confidence == 0.52


def test_degraded_payload_statuses():
    p = DegradedPayload(
        status="partial",
        available_agents=["quality"],
        missing_agents=["workflow"],
        message="timeout",
    )
    assert p.status == "partial"
    assert "quality" in p.available_agents
    assert "workflow" in p.missing_agents


def test_chat_request_stores_query():
    req = ChatRequest(query="Why did orders_pipeline fail?")
    assert req.query == "Why did orders_pipeline fail?"


def test_dag_run_defaults():
    from models import DagRun, DagStatus
    run = DagRun(dag_id="orders_pipeline", run_id="run_001", status=DagStatus.failed)
    assert run.start_date is None
    assert run.end_date is None


def test_quality_result_fields():
    from models import QualityResult
    q = QualityResult(
        table_name="orders",
        null_rate=0.025,
        row_count=1000,
        freshness_hours=0.5,
        schema_drift=False,
        score=0.92,
    )
    assert q.score == 0.92
    assert q.schema_drift is False
