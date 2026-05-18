# RAG Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the hybrid retrieval layer — an async embedder backed by OpenAI `text-embedding-3-small`, and a retriever that fuses vector similarity with PostgreSQL full-text search via Reciprocal Rank Fusion, then reranks top-8 to top-3 with Cohere. Falls back to vector-only on Cohere outage.

**Architecture:** Direct asyncpg queries against the custom DDL tables (not LangChain's auto-managed PGVector tables). `ef_search=40` set via `SET LOCAL` inside an explicit transaction so it resets at transaction end — safe for pooled connections. `airflow_embeddings` uses `'simple'` FTS config (preserves error codes); `quality_embeddings` uses `'english'`.

**Tech Stack:** asyncpg 0.29, openai 1.54 (AsyncOpenAI), cohere 5.11 (AsyncClientV2), pgvector ≥ 0.5

---

## File Map

```
backend/
├── rag/
│   ├── embedder.py      # AsyncOpenAI text-embedding-3-small, lru_cache client
│   └── retriever.py     # hybrid search (vector+FTS+RRF) + Cohere rerank + fallback
└── tests/
    └── test_retriever.py
```

---

### Task 1: Embedder

**Files:**
- Create: `backend/rag/embedder.py`

> No dedicated tests needed — the embedder is a thin wrapper tested via the retriever.

- [ ] **Step 1: Write `backend/rag/embedder.py`**

```python
from functools import lru_cache
from openai import AsyncOpenAI
from config import settings

@lru_cache(maxsize=1)
def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key, max_retries=settings.openai_max_retries)

async def embed(text: str) -> list[float]:
    resp = await _client().embeddings.create(model="text-embedding-3-small", input=text)
    return resp.data[0].embedding
```

- [ ] **Step 2: Commit**

```bash
git add backend/rag/embedder.py
git commit -m "feat: async OpenAI embedder (text-embedding-3-small)"
```

---

### Task 2: Hybrid Retriever with Cohere Reranking

**Files:**
- Create: `backend/rag/retriever.py`
- Create: `backend/tests/test_retriever.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_retriever.py
import pytest
from unittest.mock import AsyncMock, patch

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
    from rag.retriever import _hybrid_search
    # Verify fts_config is passed correctly for quality_embeddings
    with patch("rag.retriever.get_pool") as mock_pool_fn:
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock()
        mock_conn.transaction = AsyncMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        ))
        mock_pool = AsyncMock()
        mock_pool.acquire = AsyncMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        mock_pool_fn.return_value = mock_pool
        await _hybrid_search([0.1] * 1536, "null rate", "quality_embeddings",
                              None, None, fts_config="english")
        # Verify the SQL was called (fetch was called)
        assert mock_conn.fetch.called
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_retriever.py -v
```

Expected: FAIL — `rag.retriever` module not found.

- [ ] **Step 3: Write `backend/rag/retriever.py`**

```python
import asyncpg
import cohere
from config import settings

_cohere_client = cohere.AsyncClientV2(api_key=settings.cohere_api_key)
_db_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    """Singleton asyncpg pool with session-level guards on every connection."""
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=5, max_size=20,
            server_settings={
                "statement_timeout":                   str(settings.statement_timeout_ms),
                "idle_in_transaction_session_timeout": "30000",
                "lock_timeout":                        "2000",
                "application_name":                    "pipeline-observability-api",
            },
        )
    return _db_pool

async def _hybrid_search(
    query_embedding: list[float],
    query_text: str,
    table: str,
    filter_col: str | None,
    filter_val: str | None,
    k: int = 8,
    fts_config: str = "simple",
) -> list[dict]:
    """Vector similarity + FTS merged via RRF (k=60). ef_search=40 set inside transaction."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # SET LOCAL resets at transaction end — never bleeds into other pooled requests
            await conn.execute("SET LOCAL hnsw.ef_search = 40")
            prefilter = f"AND {filter_col} = $3" if filter_col else ""
            params: list = [query_embedding, query_text]
            if filter_col:
                params.append(filter_val)
            rows = await conn.fetch(f"""
                WITH vector_ranked AS (
                    SELECT id, text, source_uri, metadata,
                           ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS rank
                    FROM {table}
                    WHERE is_deleted = false {prefilter}
                    ORDER BY embedding <=> $1::vector
                    LIMIT {k * 2}
                ),
                fts_ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (ORDER BY ts_rank(fts_vector, q) DESC) AS rank
                    FROM {table},
                         websearch_to_tsquery('{fts_config}', $2) AS q
                    WHERE is_deleted = false
                      AND fts_vector @@ q {prefilter}
                    ORDER BY ts_rank(fts_vector, q) DESC
                    LIMIT {k * 2}
                ),
                rrf AS (
                    SELECT
                        COALESCE(v.id, f.id) AS id,
                        (COALESCE(1.0/(60 + v.rank), 0) + COALESCE(1.0/(60 + f.rank), 0)) AS rrf_score
                    FROM vector_ranked v
                    FULL OUTER JOIN fts_ranked f USING (id)
                )
                SELECT t.id, t.text, t.source_uri, t.metadata, rrf.rrf_score AS score
                FROM rrf
                JOIN {table} t ON t.id = rrf.id
                ORDER BY rrf_score DESC
                LIMIT {k}
            """, *params)
    return [dict(r) for r in rows]

async def _cohere_rerank(
    query: str, chunks: list[dict], top_n: int = 3
) -> tuple[list[dict], bool]:
    docs = [c["text"] for c in chunks]
    resp = await _cohere_client.rerank(
        model="rerank-english-v3.0", query=query, documents=docs, top_n=top_n
    )
    reranked = [chunks[r.index] | {"rerank_score": r.relevance_score} for r in resp.results]
    return reranked, False

async def retrieve(
    query: str,
    table: str,
    filter_col: str | None = None,
    filter_val: str | None = None,
    top_k: int = 8,
    top_n: int = 3,
    fts_config: str | None = None,
) -> tuple[list[dict], bool]:
    """Hybrid retrieval with Cohere reranking.

    Returns (chunks, rerank_fallback).
    - chunks: list of dicts with keys: id, text, source_uri, metadata, score/rerank_score
    - rerank_fallback: True when Cohere was unavailable; UI renders a banner
    """
    from rag.embedder import embed
    # Auto-select FTS config from table name if not specified
    if fts_config is None:
        fts_config = "english" if table == "quality_embeddings" else "simple"

    embedding = await embed(query)
    candidates = await _hybrid_search(embedding, query, table, filter_col, filter_val,
                                       k=top_k, fts_config=fts_config)
    if not candidates:
        return [], False

    try:
        reranked, fallback = await _cohere_rerank(query, candidates, top_n=top_n)
        threshold = settings.confidence_threshold
        score_key = "rerank_score"
    except Exception:
        # Cohere outage/timeout: return top-n by RRF score, lower threshold
        reranked = sorted(candidates, key=lambda c: c["score"], reverse=True)[:top_n]
        fallback = True
        threshold = settings.rerank_fallback_threshold
        score_key = "score"

    return [c for c in reranked if c.get(score_key, 0) >= threshold], fallback
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_retriever.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/rag/ backend/tests/test_retriever.py
git commit -m "feat: hybrid retrieval (vector+FTS+RRF) with Cohere reranking and fallback"
```
