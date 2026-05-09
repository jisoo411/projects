from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Required
    openai_api_key: str = ""
    cohere_api_key: str = ""
    database_url: str = ""        # pipeline_watch DB (Neon)
    source_database_url: str = "" # source data DB (Neon, same project)
    airflow_base_url: str = ""
    api_key: str = ""
    audit_hmac_secret: str = ""
    frontend_origin: str = "http://localhost:3000"

    # LangSmith (optional — tracing disabled if not set)
    langchain_api_key: str = ""
    langchain_project: str = "pipeline-watch"
    langchain_tracing_v2: str = "true"

    # Airflow auth (token preferred; basic-auth fallback)
    airflow_token: str = ""
    airflow_username: str = ""
    airflow_password: str = ""

    # Calibrated thresholds
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
