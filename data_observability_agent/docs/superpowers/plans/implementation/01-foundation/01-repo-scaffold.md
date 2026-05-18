# Repository Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the `pipeline-watch` repository with the full backend directory structure, pinned dependencies, Pydantic settings, shared data models, and a versioned confidence-threshold config file.

**Architecture:** `pydantic-settings` reads a `.env` file for all required and optional config. `models.py` defines all shared Pydantic schemas used by agents, the API, and the frontend TypeScript types. `config/thresholds.yaml` stores calibrated RAG confidence thresholds — versioned so they can be re-calibrated without code changes.

**Tech Stack:** Python 3.11, FastAPI 0.115, pydantic-settings 2.6, pydantic 2.9, PyYAML

---

## File Map

```
pipeline-watch/
├── backend/
│   ├── requirements.txt          # all pinned dependencies
│   ├── .env.example              # required + optional vars with inline docs
│   ├── config.py                 # pydantic-settings: reads .env
│   ├── models.py                 # shared Pydantic schemas
│   ├── config/
│   │   └── thresholds.yaml       # calibrated confidence thresholds (versioned)
│   ├── routers/  __init__.py
│   ├── rag/      __init__.py
│   ├── tools/    __init__.py
│   ├── agents/   __init__.py
│   ├── guardrails/ __init__.py
│   ├── ingestion/  __init__.py
│   ├── airflow_mcp/ __init__.py
│   └── tests/
│       └── conftest.py
```

---

### Task 1: Clone Starter and Create Directory Structure

**Files:**
- Create: all `__init__.py` package markers
- Create: `backend/tests/conftest.py`

- [ ] **Step 1: Clone the starter and wire a fresh remote**

```bash
git clone https://github.com/DataExpert-io/rag-vercel-example pipeline-watch
cd pipeline-watch
git remote remove origin
# Create a new GitHub repo named pipeline-watch, then:
git remote add origin https://github.com/<your-username>/pipeline-watch.git
```

- [ ] **Step 2: Create the backend package structure**

```bash
mkdir -p backend/routers backend/rag backend/tools backend/agents \
          backend/guardrails backend/ingestion backend/airflow_mcp \
          backend/tests backend/migrations backend/config
touch backend/routers/__init__.py backend/rag/__init__.py \
      backend/tools/__init__.py backend/agents/__init__.py \
      backend/guardrails/__init__.py backend/ingestion/__init__.py \
      backend/airflow_mcp/__init__.py
```

- [ ] **Step 3: Write `backend/tests/conftest.py`**

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
```

- [ ] **Step 4: Commit**

```bash
git add backend/
git commit -m "chore: create backend package structure"
```

---

### Task 2: Dependencies

**Files:**
- Create: `backend/requirements.txt`

- [ ] **Step 1: Write `backend/requirements.txt`**

```text
# Web framework
fastapi==0.115.0
uvicorn[standard]==0.30.6

# Agent framework
langgraph==0.2.28
langchain==0.3.7
langchain-openai==0.2.9
langchain-core==0.3.17
langchain-mcp-adapters==0.1.3

# MCP + Airflow
mcp==1.3.0
apache-airflow-client==3.0.0

# LLM / embeddings / reranking
openai==1.54.0
cohere==5.11.0

# Database
asyncpg==0.29.0
pgvector==0.3.5

# Guardrails + PII
guardrails-ai==0.5.14
presidio-analyzer==2.2.354
presidio-anonymizer==2.2.354
spacy==3.7.6
# After install: python -m spacy download en_core_web_sm

# Observability
langsmith==0.1.147

# Scheduling
apscheduler==3.10.4

# Config + utils
pydantic==2.9.2
pydantic-settings==2.6.0
python-dotenv==1.0.1
httpx==0.27.2
pyyaml==6.0.2

# Testing
pytest==8.3.3
pytest-asyncio==0.24.0
```

- [ ] **Step 2: Install dependencies**

```bash
cd backend && pip install -r requirements.txt && python -m spacy download en_core_web_sm
```

Expected: all packages install without conflicts.

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore: pin all backend dependencies"
```

---

### Task 3: Config and Environment

**Files:**
- Create: `backend/.env.example`
- Create: `backend/config.py`
- Create: `backend/config/thresholds.yaml`

- [ ] **Step 1: Write `backend/.env.example`**

```bash
# Copy to .env and fill in real values: cp backend/.env.example backend/.env

# ── REQUIRED ──────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...
COHERE_API_KEY=...
DATABASE_URL=postgresql://user:password@localhost:5432/pipeline_watch
AIRFLOW_BASE_URL=http://localhost:8080
AIRFLOW_TOKEN=...                    # preferred: Airflow 3 OAuth token
# AIRFLOW_USERNAME=admin             # basic-auth fallback (omit if using token)
# AIRFLOW_PASSWORD=admin
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=pipeline-watch
API_KEY=...                          # shared API key for all FastAPI endpoints
AUDIT_HMAC_SECRET=...                # server-side HMAC secret — rotating invalidates audit_log correlation IDs
FRONTEND_ORIGIN=https://pipeline-watch.vercel.app  # used for CORS; set to http://localhost:3000 in dev

# ── OPTIONAL (defaults shown) ──────────────────────────────────────────────
CONFIDENCE_THRESHOLD=0.6             # Cohere rerank score threshold (see config/thresholds.yaml)
RERANK_FALLBACK_THRESHOLD=0.5        # pgvector cosine threshold used when Cohere is unavailable
STATEMENT_TIMEOUT_MS=10000           # DB statement_timeout in milliseconds
OPENAI_MAX_RETRIES=3                 # exponential backoff retries on 429/5xx
SSE_HEARTBEAT_INTERVAL_S=15          # keep-alive ping interval for SSE connections
MAX_LOG_PAGES=10                     # cap on Airflow log pagination
MAX_CONCURRENT_LIVE_CHECKS=3         # asyncio semaphore for on-demand DB quality checks
INGEST_INTERVAL_MINUTES=15
```

- [ ] **Step 2: Write `backend/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="backend/.env", extra="ignore")

    # Required
    openai_api_key: str
    cohere_api_key: str
    database_url: str
    airflow_base_url: str
    api_key: str
    audit_hmac_secret: str
    frontend_origin: str = "http://localhost:3000"

    # LangSmith (optional — tracing disabled if not set)
    langchain_api_key: str = ""
    langchain_project: str = "pipeline-watch"
    langchain_tracing_v2: str = "true"

    # Airflow auth (token preferred; basic-auth fallback)
    airflow_token: str = ""
    airflow_username: str = ""
    airflow_password: str = ""

    # Calibrated thresholds (also in config/thresholds.yaml for audit trail)
    confidence_threshold: float = 0.6
    rerank_fallback_threshold: float = 0.5

    # Operational limits
    statement_timeout_ms: int = 10000
    openai_max_retries: int = 3
    sse_heartbeat_interval_s: int = 15
    max_log_pages: int = 10
    max_concurrent_live_checks: int = 3
    ingest_interval_minutes: int = 15

settings = Settings()
```

- [ ] **Step 3: Write `backend/config/thresholds.yaml`**

```yaml
# Confidence threshold configuration
# Versioned: update this file and bump `version` whenever thresholds are re-calibrated.
# Calibration procedure: evaluate against the LangSmith golden dataset (100+ labeled queries).
# Use the F1-maximising operating point. Re-calibrate whenever embedding or reranker model changes.

version: "1.0.0"
calibrated_against: "rerank-english-v3.0 + text-embedding-3-small"
calibration_date: "2026-04-22"

thresholds:
  normal:
    value: 0.6
    description: "Cohere relevance_score threshold on normal reranking path"
  fallback:
    value: 0.5
    description: "pgvector cosine similarity threshold when Cohere is unavailable"

ab_test:
  enabled: false
  candidate_normal: 0.65
  candidate_fallback: 0.55
  description: "Set enabled=true to test candidate thresholds against the golden dataset"
```

- [ ] **Step 4: Verify no import errors**

```bash
cd backend && python -c "from config import settings; print('Settings OK:', settings.confidence_threshold)"
```

Expected: `Settings OK: 0.6`

- [ ] **Step 5: Commit**

```bash
git add backend/.env.example backend/config.py backend/config/thresholds.yaml
git commit -m "feat: pydantic-settings config + thresholds.yaml"
```

---

### Task 4: Shared Pydantic Models

**Files:**
- Create: `backend/models.py`
- Create: `backend/tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_models.py
from models import Citation, ChatResponse, DegradedPayload, ChatRequest

def test_citation_truncates_excerpt_at_500():
    from pydantic import ValidationError
    import pytest
    with pytest.raises(ValidationError):
        Citation(source_id="airflow:dag=x/task=y/run=z/try=1/ts=2026-01-01T00:00:00Z",
                 excerpt="x" * 501)

def test_chat_response_defaults():
    r = ChatResponse(
        summary="ok", current_state="healthy", root_causes=[], recommended_actions=[],
        citations=[Citation(
            source_id="airflow:dag=orders/task=extract/run=r1/try=1/ts=2026-01-01T00:00:00Z",
            excerpt="task failed"
        )]
    )
    assert r.confidence == 0.0
    assert r.degraded_reranking is False

def test_degraded_payload_statuses():
    from models import DegradedPayload
    p = DegradedPayload(status="partial", available_agents=["quality"],
                        missing_agents=["workflow"], message="timeout")
    assert p.status == "partial"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_models.py -v
```

Expected: FAIL — `models` module not found.

- [ ] **Step 3: Write `backend/models.py`**

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal
from enum import Enum

class DagStatus(str, Enum):
    success = "success"
    failed = "failed"
    running = "running"
    unknown = "unknown"

class DagRun(BaseModel):
    dag_id: str
    run_id: str
    status: DagStatus
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class QualityResult(BaseModel):
    table_name: str
    null_rate: float
    row_count: int
    freshness_hours: float
    schema_drift: bool
    score: float

class StatusResponse(BaseModel):
    dags: list[DagRun]
    quality: list[QualityResult]
    healthy_dag_count: int
    failed_dag_count: int

class Citation(BaseModel):
    source_id: str   # airflow:dag=.../task=.../run=.../try=.../ts=... OR dbcheck:table=.../metric=.../ts=...
    excerpt: str = Field(..., max_length=500)

class ChatResponse(BaseModel):
    summary: str
    current_state: str
    root_causes: list[str]
    recommended_actions: list[str]
    citations: list[Citation]
    confidence: float = 0.0          # Cohere relevance_score [0,1]; 1-cosine_distance on fallback
    degraded_reranking: bool = False  # True when Cohere fallback used; triggers UI banner

class DegradedPayload(BaseModel):
    status: Literal["partial", "degraded"]
    available_agents: list[str]
    missing_agents: list[str]
    message: str

class ChatRequest(BaseModel):
    query: str

class FeedbackRequest(BaseModel):
    run_id: str
    score: int       # 1 = thumbs up, 0 = thumbs down
    comment: Optional[str] = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_models.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/tests/test_models.py
git commit -m "feat: shared Pydantic models (ChatResponse, Citation, DegradedPayload)"
```
