import asyncio
import json
from datetime import datetime, timezone

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from rag.retriever import get_pool


class QualityCheckInput(BaseModel):
    table_name: str
    timestamp_column: str = "updated_at"
    nullable_column: str = "id"


async def _compute_quality_metrics_async(
    table_name: str, timestamp_column: str, nullable_column: str
) -> dict:
    pool = await get_pool()
    ts = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        row_count = await conn.fetchval(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608
        null_count = await conn.fetchval(
            f"SELECT COUNT(*) FROM {table_name} WHERE {nullable_column} IS NULL"  # noqa: S608
        )
        max_ts = await conn.fetchval(f"SELECT MAX({timestamp_column}) FROM {table_name}")  # noqa: S608
        schema_drift_col = await conn.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = $1 AND data_type IN ('json','jsonb') LIMIT 1",
            table_name,
        )

    null_rate = round(null_count / row_count, 4) if row_count > 0 else 0.0
    freshness_hours = 999.0
    if max_ts is not None:
        if isinstance(max_ts, str):
            from datetime import datetime as dt
            max_ts = dt.fromisoformat(max_ts)
        if hasattr(max_ts, "tzinfo") and max_ts.tzinfo is None:
            max_ts = max_ts.replace(tzinfo=timezone.utc)
        freshness_hours = round((ts - max_ts).total_seconds() / 3600, 2)

    schema_drift = schema_drift_col is not None
    score = round(
        (1.0 - null_rate) * 0.4
        + (1.0 if freshness_hours < 24 else 0.0) * 0.4
        + (0.0 if schema_drift else 1.0) * 0.2,
        4,
    )
    source_uri = (
        f"dbcheck:table={table_name}/metric=composite"
        f"/ts={ts.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    return {
        "table_name": table_name,
        "row_count": row_count,
        "null_rate": null_rate,
        "freshness_hours": freshness_hours,
        "schema_drift": schema_drift,
        "score": score,
        "source_uri": source_uri,
        "observed_at": ts.isoformat(),
    }


async def get_cached_metrics(table_name: str, schema_name: str = "public") -> dict | None:
    """Read the latest snapshot from live_metrics. Returns None before first ingestion cycle."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT table_name, metric_type, value, status, observed_at, source "
            "FROM live_metrics "
            "WHERE schema_name = $1 AND table_name = $2 "
            "ORDER BY observed_at DESC LIMIT 1",
            schema_name,
            table_name,
        )
    return dict(row) if row else None


def _run_quality_check_sync(
    table_name: str, timestamp_column: str, nullable_column: str
) -> str:
    result = asyncio.run(
        _compute_quality_metrics_async(table_name, timestamp_column, nullable_column)
    )
    return json.dumps(result, indent=2)


def get_quality_tools() -> list:
    return [
        StructuredTool(
            name="run_data_quality_check",
            description=(
                "Run live data quality checks on a DB table. Returns null rate, row count, "
                "freshness hours, schema drift flag, quality score (0–1), and a source URI. "
                "Only call this when the user explicitly names a specific table."
            ),
            args_schema=QualityCheckInput,
            func=_run_quality_check_sync,
        )
    ]
