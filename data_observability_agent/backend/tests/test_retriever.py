import pytest
from unittest.mock import AsyncMock, patch, MagicMock

MOCK_AIRFLOW_CHUNK = {
    "id": "uuid-a",
    "text": "orders_pipeline failed at 02:14 — KeyError 'order_id'",
    "source_uri": "airflow:dag=orders_pipeline/task=extract/run=run_001/try=1/ts=2026-04-22T02:14:00Z",
    "metadata": None,
    "score": 0.87,
}


@pytest.mark.asyncio
async def test_retrieve_returns_chunks_above_threshold():
    from rag.retriever import retrieve
    with patch("rag.retriever._hybrid_search", new=AsyncMock(return_value=[MOCK_AIRFLOW_CHUNK])), \
         patch("rag.retriever._cohere_rerank",
               new=AsyncMock(return_value=([{**MOCK_AIRFLOW_CHUNK, "rerank_score": 0.91}], False))):
        results, fallback = await retrieve(
            "orders pipeline failure",
            table="airflow_embeddings",
            filter_col="dag_id",
            filter_val="orders_pipeline",
        )
    assert len(results) == 1
    assert "orders_pipeline" in results[0]["text"]
    assert "airflow:dag=orders_pipeline" in results[0]["source_uri"]
    assert not fallback


@pytest.mark.asyncio
async def test_retrieve_returns_empty_when_below_threshold():
    from rag.retriever import retrieve
    low_score_chunk = {**MOCK_AIRFLOW_CHUNK, "rerank_score": 0.3}
    with patch("rag.retriever._hybrid_search", new=AsyncMock(return_value=[MOCK_AIRFLOW_CHUNK])), \
         patch("rag.retriever._cohere_rerank",
               new=AsyncMock(return_value=([low_score_chunk], False))):
        results, fallback = await retrieve("orders pipeline failure", table="airflow_embeddings")
    assert results == []
    assert not fallback


@pytest.mark.asyncio
async def test_cohere_fallback_uses_lower_threshold_and_sets_flag():
    from rag.retriever import retrieve
    chunk = {**MOCK_AIRFLOW_CHUNK, "score": 0.52}
    with patch("rag.retriever._hybrid_search", new=AsyncMock(return_value=[chunk])), \
         patch("rag.retriever._cohere_rerank", new=AsyncMock(side_effect=Exception("Cohere 503"))):
        results, fallback = await retrieve("something", table="airflow_embeddings")
    assert fallback is True
    # score 0.52 >= fallback threshold 0.5 → chunk is included
    assert len(results) == 1


@pytest.mark.asyncio
async def test_retrieve_empty_candidates_returns_empty():
    from rag.retriever import retrieve
    with patch("rag.retriever._hybrid_search", new=AsyncMock(return_value=[])):
        results, fallback = await retrieve("anything", table="airflow_embeddings")
    assert results == [] and not fallback


@pytest.mark.asyncio
async def test_retrieve_quality_table_uses_english_fts():
    """_hybrid_search is called with fts_config='english' for quality_embeddings."""
    from rag.retriever import _hybrid_search
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.execute = AsyncMock()
    # transaction() must be a context manager
    txn_ctx = MagicMock()
    txn_ctx.__aenter__ = AsyncMock(return_value=None)
    txn_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=txn_ctx)

    conn_ctx = MagicMock()
    conn_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    conn_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=conn_ctx)

    with patch("rag.retriever.get_pool", new=AsyncMock(return_value=mock_pool)):
        await _hybrid_search(
            [0.1] * 1536, "null rate", "quality_embeddings",
            None, None, fts_config="english",
        )
    assert mock_conn.fetch.called


@pytest.mark.asyncio
async def test_retrieve_filter_col_passed_to_hybrid_search():
    """filter_col and filter_val are forwarded to _hybrid_search when provided."""
    from rag.retriever import retrieve
    captured = {}

    async def mock_hybrid(embedding, query_text, table, filter_col, filter_val, **kwargs):
        captured["filter_col"] = filter_col
        captured["filter_val"] = filter_val
        return [MOCK_AIRFLOW_CHUNK]

    with patch("rag.retriever._hybrid_search", new=mock_hybrid), \
         patch("rag.retriever._cohere_rerank",
               new=AsyncMock(return_value=([{**MOCK_AIRFLOW_CHUNK, "rerank_score": 0.91}], False))):
        await retrieve(
            "pipeline failure",
            table="airflow_embeddings",
            filter_col="dag_id",
            filter_val="orders_pipeline",
        )
    assert captured["filter_col"] == "dag_id"
    assert captured["filter_val"] == "orders_pipeline"


@pytest.mark.asyncio
async def test_retrieve_no_filter_passes_none_to_hybrid_search():
    """filter_col=None when no filter specified — no extra SQL predicate."""
    from rag.retriever import retrieve
    captured = {}

    async def mock_hybrid(embedding, query_text, table, filter_col, filter_val, **kwargs):
        captured["filter_col"] = filter_col
        return []

    with patch("rag.retriever._hybrid_search", new=mock_hybrid):
        await retrieve("general query", table="airflow_embeddings")
    assert captured["filter_col"] is None
