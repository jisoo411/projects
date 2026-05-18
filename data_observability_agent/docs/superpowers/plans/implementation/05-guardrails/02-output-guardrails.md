# Output Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the output guardrail layer — structured source citation enforcement and a lightweight hallucination check. Ensures every response cites at least one valid `airflow:` or `dbcheck:` URI, and that any table name or DAG name in the response was actually mentioned in the retrieved context passed to the agent.

**Architecture:** Two sequential checks: (1) citation enforcer — regex-scans the response for valid source URI format and rejects if none found; (2) hallucination checker — extracts entity names from the response and verifies each appears in the supplied context string. Returns `OutputGuardrailResult` with `blocked`, `block_reason`, and the validated `response`.

**Tech Stack:** Python `re`, no external dependencies beyond stdlib

---

## File Map

```
backend/
├── guardrails/
│   └── output_guardrails.py
└── tests/
    └── test_output_guardrails.py
```

---

### Task 1: Write Failing Tests

**Files:**
- Create: `backend/tests/test_output_guardrails.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_output_guardrails.py
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_output_guardrails.py -v
```

Expected: FAIL — `guardrails.output_guardrails` not found.

---

### Task 2: Implement Output Guardrails

**Files:**
- Create: `backend/guardrails/output_guardrails.py`

- [ ] **Step 1: Write `backend/guardrails/output_guardrails.py`**

```python
import re
from dataclasses import dataclass

@dataclass
class OutputGuardrailResult:
    blocked: bool
    block_reason: str | None   # "missing_citations" | "hallucination" | None
    response: str

# Matches: airflow:dag=.../task=.../run=.../try=.../ts=...
#      or: dbcheck:table=.../metric=.../ts=...
_VALID_URI_RE = re.compile(
    r"(?:airflow:dag=[^/\s]+/task=[^/\s]+/run=[^/\s]+/try=\d+/ts=\S+"
    r"|dbcheck:table=[^/\s]+/metric=[^/\s]+/ts=\S+)"
)

# Extracts dag_id tokens from airflow: URIs in the response
_AIRFLOW_DAG_RE = re.compile(r"airflow:dag=([^/\s]+)")
# Extracts table_name tokens from dbcheck: URIs in the response
_DBCHECK_TABLE_RE = re.compile(r"dbcheck:table=([^/\s]+)")

def _has_valid_citations(response: str) -> bool:
    """Return True if at least one properly-formatted source URI is present."""
    return bool(_VALID_URI_RE.search(response))

def _check_hallucination(response: str, context: str) -> str | None:
    """Return the first entity name cited in the response that is absent from context.
    Returns None when everything checks out."""
    context_lower = context.lower()
    for dag_id in _AIRFLOW_DAG_RE.findall(response):
        if dag_id.lower() not in context_lower:
            return dag_id
    for table_name in _DBCHECK_TABLE_RE.findall(response):
        if table_name.lower() not in context_lower:
            return table_name
    return None

def check_output(response: str, context: str) -> OutputGuardrailResult:
    """Validate the agent response before returning it to the user.

    Args:
        response: The text produced by the orchestrator.
        context:  The combined RAG context and sub-agent outputs used to generate it.

    Returns:
        OutputGuardrailResult with blocked=True if citations are missing or a
        hallucinated entity is detected.
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
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_output_guardrails.py -v
```

Expected: PASS (6 tests)

- [ ] **Step 3: Commit**

```bash
git add backend/guardrails/output_guardrails.py backend/tests/test_output_guardrails.py
git commit -m "feat: output guardrails (structured URI citation enforcement, hallucination entity check)"
```
