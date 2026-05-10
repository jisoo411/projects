import re
from dataclasses import dataclass


@dataclass
class OutputGuardrailResult:
    blocked: bool
    block_reason: str | None  # "missing_citations" | "hallucination" | None
    response: str


# airflow:dag=.../task=.../run=.../try=N/ts=...
# dbcheck:table=.../metric=.../ts=...
_VALID_URI_RE = re.compile(
    r"(?:airflow:dag=[^/\s]+/task=[^/\s]+/run=[^/\s]+/try=\d+/ts=\S+"
    r"|dbcheck:table=[^/\s]+/metric=[^/\s]+/ts=\S+)"
)

_AIRFLOW_DAG_RE = re.compile(r"airflow:dag=([^/\s]+)")
_DBCHECK_TABLE_RE = re.compile(r"dbcheck:table=([^/\s]+)")


def _has_valid_citations(response: str) -> bool:
    return bool(_VALID_URI_RE.search(response))


def _check_hallucination(response: str, context: str) -> str | None:
    """Return the first cited entity absent from context, or None if all check out."""
    context_lower = context.lower()
    for dag_id in _AIRFLOW_DAG_RE.findall(response):
        if dag_id.lower() not in context_lower:
            return dag_id
    for table_name in _DBCHECK_TABLE_RE.findall(response):
        if table_name.lower() not in context_lower:
            return table_name
    return None


def check_output(response: str, context: str) -> OutputGuardrailResult:
    """Validate agent response before returning to the user.

    Checks: (1) at least one valid airflow:/dbcheck: URI present,
            (2) no cited entity is absent from the supplied context.
    """
    if not _has_valid_citations(response):
        return OutputGuardrailResult(
            blocked=True, block_reason="missing_citations", response=response
        )

    hallucinated = _check_hallucination(response, context)
    if hallucinated:
        return OutputGuardrailResult(
            blocked=True, block_reason="hallucination", response=response
        )

    return OutputGuardrailResult(blocked=False, block_reason=None, response=response)
