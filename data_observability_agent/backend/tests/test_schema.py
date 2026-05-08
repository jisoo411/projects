import asyncio
import asyncpg
from config import settings


async def _q(sql: str):
    conn = await asyncpg.connect(settings.database_url)
    result = await conn.fetchval(sql)
    await conn.close()
    return result


def test_vector_extension_installed():
    r = asyncio.run(_q("SELECT extname FROM pg_extension WHERE extname='vector'"))
    assert r is not None, "pgvector not installed — run migrations/001_schema.sql"


def test_pgcrypto_extension_installed():
    r = asyncio.run(_q("SELECT extname FROM pg_extension WHERE extname='pgcrypto'"))
    assert r is not None, "pgcrypto not installed — run migrations/001_schema.sql"


def test_all_six_tables_exist():
    expected = sorted([
        'airflow_embeddings', 'quality_embeddings', 'live_metrics',
        'quality_metrics_history', 'dag_status_cache', 'audit_log',
    ])
    actual = asyncio.run(_q(
        "SELECT array_agg(table_name::text ORDER BY table_name) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name = ANY(ARRAY["
        "'airflow_embeddings','quality_embeddings','live_metrics',"
        "'quality_metrics_history','dag_status_cache','audit_log'])"
    ))
    assert sorted(actual) == expected, f"Missing tables: {set(expected) - set(actual)}"


def test_airflow_embeddings_soft_delete_columns():
    cols = asyncio.run(_q(
        "SELECT array_agg(column_name::text ORDER BY column_name) "
        "FROM information_schema.columns "
        "WHERE table_name='airflow_embeddings' AND column_name IN ('is_deleted','deleted_at')"
    ))
    assert sorted(cols) == ['deleted_at', 'is_deleted']


def test_airflow_embeddings_fts_vector_column():
    col = asyncio.run(_q(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='airflow_embeddings' AND column_name='fts_vector'"
    ))
    assert col == 'fts_vector', "fts_vector generated column missing"


def test_quality_embeddings_soft_delete_columns():
    cols = asyncio.run(_q(
        "SELECT array_agg(column_name::text ORDER BY column_name) "
        "FROM information_schema.columns "
        "WHERE table_name='quality_embeddings' AND column_name IN ('is_deleted','deleted_at')"
    ))
    assert sorted(cols) == ['deleted_at', 'is_deleted']


def test_live_metrics_unique_index():
    idx = asyncio.run(_q(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename='live_metrics' AND indexdef LIKE '%schema_name%table_name%metric_type%'"
    ))
    assert idx is not None, "unique index on live_metrics (schema_name, table_name, metric_type) missing"


def test_freshness_index_on_orders():
    idx = asyncio.run(_q(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename='orders' AND indexdef LIKE '%updated_at%'"
    ))
    assert idx is not None, "freshness index on orders(updated_at) missing"
