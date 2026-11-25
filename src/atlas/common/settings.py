"""Central, typed configuration (12-factor: everything comes from the environment)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev | ci | prod
    service_name: str = "atlas"
    log_level: str = "INFO"
    config_dir: Path = Path("config")

    # --- datastores -------------------------------------------------------
    database_url: str = "postgresql+asyncpg://atlas_app:atlas_app@localhost:5432/atlas"
    # Owner role used only by migrations and admin bootstrap (bypasses RLS).
    database_admin_url: str = "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    s3_endpoint: str = "localhost:9000"
    s3_access_key: str = "atlas"
    s3_secret_key: SecretStr = SecretStr("atlas-secret")
    s3_bucket: str = "atlas"
    s3_secure: bool = False

    # --- model servers ----------------------------------------------------
    embed_url: str = "http://localhost:8081"  # TEI /embed
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    embed_batch_size: int = 64
    rerank_url: str = "http://localhost:8082"  # TEI /rerank
    rerank_enabled: bool = True
    rerank_candidates: int = 16      # cross-encoders are O(candidates × length): the top of the fused list only
    rerank_max_chars: int = 700      # ~175 tokens per passage keeps CPU reranking sub-second-ish
    rerank_batch: int = 32           # TEI rejects larger requests (max_client_batch_size)

    # --- gateway ----------------------------------------------------------
    gateway_url: str = "http://localhost:8090"
    gateway_internal_key: SecretStr = SecretStr("dev-internal-key")
    gateway_config_file: str = "gateway.yaml"
    pricing_file: str = "pricing.yaml"
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None
    ollama_base_url: str = "http://ollama:11434/v1"
    mock_base_url: str = "http://mockllm:8099/v1"

    # --- security ---------------------------------------------------------
    bootstrap_token: SecretStr = SecretStr("change-me-bootstrap")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    max_upload_mb: int = 50

    # --- ingestion --------------------------------------------------------
    parser: str = "native"  # native | docling
    parse_timeout_s: int = 300
    # --- throughput knobs (scale out further with `docker compose up --scale worker-ingest=N`)
    ingest_concurrency: int = 8      # concurrent ingest jobs per worker process
    parse_workers: int = 2           # CPU processes for PDF/Office parsing (bypasses the GIL)
    embed_concurrency: int = 4       # concurrent embedding batches in flight to TEI
    index_batch_size: int = 256      # points per Qdrant upsert
    pipeline_version: str = "2026.11.1"
    contextual_enrichment: bool = True

    # --- retrieval defaults (overridable per collection) --------------------
    retrieval_candidates: int = 50
    retrieval_top_k: int = 6
    context_token_budget: int = 3500
    abstain_threshold: float = 0.05

    # --- memory -----------------------------------------------------------
    memory_buffer_tokens: int = 2000
    memory_facts_top_k: int = 5

    # --- telemetry --------------------------------------------------------
    otlp_endpoint: str | None = None  # Pulse server (or any OTLP/HTTP collector), e.g. http://pulse:8100
    otlp_api_key: str | None = None  # Pulse project key
    capture_content: bool = False  # prompts/answers in traces (PII-redacted); enable for dev/demo only

    # --- console / demo ---------------------------------------------------
    demo_mode: bool = False  # enables POST /v1/demo/session (one-click demo tenant). NEVER in prod.
    pulse_public_url: str = "/pulse"  # how the browser console reaches Pulse
    llm_live: bool = False  # mirrors ATLAS_LLM_LIVE (real providers on/off); shown in the console

    def config_path(self, name: str) -> Path:
        return self.config_dir / name


@lru_cache
def get_settings() -> Settings:
    return Settings()
