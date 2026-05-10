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
