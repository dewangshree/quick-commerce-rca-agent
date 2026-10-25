"""Application settings, loaded from environment variables / .env.

Every external dependency (database, Ollama models, timeouts) is configurable
here. There is deliberately no setting for a paid LLM provider: if Ollama is
unavailable the platform degrades to deterministic output instead of calling
a cloud API.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "development"

    # Primary (read/write) and analytics (read-only) connections.
    database_url: str = "postgresql+psycopg://qc_app:qc_app_dev@localhost:5432/qc_rca"
    readonly_database_url: str | None = None
    db_statement_timeout_ms: int = 5000
    db_pool_size: int = 5

    data_csv_path: Path = ROOT_DIR / "data" / "quick_commerce_orders_gold_20260422.csv"
    knowledge_base_dir: Path = ROOT_DIR / "knowledge_base"

    # Local models via Ollama.
    ollama_base_url: str = "http://localhost:11434"
    llm_enabled: bool = True
    llm_model: str = "qwen2.5:1.5b"
    llm_timeout_s: float = 45.0
    llm_max_retries: int = Field(default=1, ge=0, le=3)
    llm_num_ctx: int = 4096
    # Off by default: in scripts/llm_probe.py qwen2.5:1.5b's numbers were grounded but it
    # mislabelled OR2A as "order fulfillment time" (see docs/EVALUATION.md).
    llm_narrative_enabled: bool = False
    embedding_model: str = "all-minilm"
    embedding_dim: int = 384
    embedding_timeout_s: float = 20.0

    # RAG
    rag_top_k: int = 4
    rag_chunk_size: int = 900
    rag_chunk_overlap: int = 150

    # Security
    auth_enabled: bool = True
    # Public demo: requests without a key act as an anonymous per-browser "visitor" with this role.
    # Admin endpoints still require a real key. Rate limits apply per client IP.
    public_demo: bool = False
    public_demo_role: str = Field(default="analyst", pattern="^(viewer|analyst)$")
    # Serve the built React app from this directory at "/" (single-URL deployments).
    frontend_dist: Path | None = None
    api_key_pepper: SecretStr = SecretStr("change-me-in-production")
    rate_limit_per_minute: int = 20
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Data retention (days; 0 = keep forever). Purged at API startup and every retention_interval_hours.
    investigation_retention_days: int = Field(default=30, ge=0)
    audit_retention_days: int = Field(default=90, ge=0)
    retention_interval_hours: float = Field(default=6, gt=0)

    # Workflow
    checkpoint_enabled: bool = True
    max_question_chars: int = 1000

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _production_guard(self):
        if self.env == "production":
            if not self.auth_enabled:
                raise ValueError("AUTH_ENABLED=false is not allowed when ENV=production")
            if self.api_key_pepper.get_secret_value() in ("change-me-in-production", "change-me"):
                raise ValueError("Set a real API_KEY_PEPPER when ENV=production")
        return self

    @property
    def analytics_database_url(self) -> str:
        return self.readonly_database_url or self.database_url

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
