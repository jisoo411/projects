from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import asyncpg
import cohere

from config import settings

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()

_TABLE_FTS_CONFIG: dict[str, str] = {
    "airflow_embeddings": "simple",
    "quality_embeddings": "english",
}


@lru_cache(maxsize=1)
def _cohere_client() -> cohere.AsyncClientV2:
    return cohere.AsyncClientV2(api_key=settings.cohere_api_key)


async def get_pool() -> asyncpg.Pool:
    global _pool
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                settings.database_url,
                min_size=1,
                max_size=5,
                server_settings={
                    "statement_timeout": str(settings.statement_timeout_ms),
                    "idle_in_transaction_session_timeout": "30000",
                    "lock_timeout": "5000",
                },
            )
    return _pool


async def _hybrid_search(
    embedding: list[float],
    query_text: str,
    table: str,
    filter_col: str | None,
    filter_val: str | None,
    *,
    fts_config: str = "simple",
    top_k: int = 8,
) -> list[dict[str, Any]]:
    pool = await get_pool()
    filter_clause = f"AND {filter_col} = $4" if filter_col else ""
    params_base: list[Any] = [str(embedding), query_text, top_k]
    if filter_col:
        params_base.append(filter_val)

    sql = f"""
    WITH vector_scores AS (
        SELECT id, 1 - (embedding <=> $1::vector) AS vscore
        FROM {table}
        WHERE is_deleted = false {filter_clause}
        ORDER BY embedding <=> $1::vector
        LIMIT $3
    ),
    fts_scores AS (
        SELECT id, ts_rank_cd(fts_vector, plainto_tsquery('{fts_config}', $2)) AS fscore
        FROM {table}
        WHERE is_deleted = false
          AND fts_vector @@ plainto_tsquery('{fts_config}', $2)
          {filter_clause}
        LIMIT $3
    ),
    rrf AS (
        SELECT
            COALESCE(v.id, f.id) AS id,
            (COALESCE(1.0 / (60 + ROW_NUMBER() OVER (ORDER BY v.vscore DESC NULLS LAST)), 0) +
             COALESCE(1.0 / (60 + ROW_NUMBER() OVER (ORDER BY f.fscore DESC NULLS LAST)), 0)) AS rrf_score
        FROM vector_scores v
        FULL OUTER JOIN fts_scores f ON v.id = f.id
    )
    SELECT e.id::text, e.text, e.source_uri, e.metadata, r.rrf_score AS score
    FROM rrf r
    JOIN {table} e ON e.id = r.id
    ORDER BY r.rrf_score DESC
    LIMIT $3
    """  # noqa: S608

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL hnsw.ef_search = 40")
            rows = await conn.fetch(sql, *params_base)

    return [
        {
            "id": row["id"],
            "text": row["text"],
            "source_uri": row["source_uri"],
            "metadata": row["metadata"],
            "score": float(row["score"]),
        }
        for row in rows
    ]


async def _cohere_rerank(
    query: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    docs = [c["text"] for c in candidates]
    resp = await _cohere_client().rerank(
        model="rerank-english-v3.0",
        query=query,
        documents=docs,
        top_n=min(3, len(docs)),
    )
    reranked = [
        {**candidates[r.index], "rerank_score": r.relevance_score}
        for r in resp.results
    ]
    return reranked, False


async def retrieve(
    query: str,
    *,
    table: str,
    filter_col: str | None = None,
    filter_val: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    from rag.embedder import embed
    embedding = await embed(query)
    fts_config = _TABLE_FTS_CONFIG.get(table, "simple")

    candidates = await _hybrid_search(
        embedding, query, table, filter_col, filter_val, fts_config=fts_config
    )
    if not candidates:
        return [], False

    try:
        reranked, fallback = await _cohere_rerank(query, candidates)
        threshold = settings.confidence_threshold
        return [r for r in reranked if r["rerank_score"] >= threshold], fallback
    except Exception:
        fallback_threshold = settings.rerank_fallback_threshold
        return [c for c in candidates if c["score"] >= fallback_threshold], True
