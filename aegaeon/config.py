from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by the controller and orchestration services."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="AEGAEON_", extra="ignore", case_sensitive=False
    )

    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = "sqlite:///./data/aegaeon.db"
    data_dir: Path = Path("./data")
    worker_token: str = Field(default="development-token", min_length=8)
    allow_development_worker_token: bool = False
    control_token: str = ""
    trusted_proxy_ips: list[str] = Field(default_factory=list)
    ui_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )
    demo_mode: bool = True
    allow_unisolated_verification: bool = False
    heartbeat_timeout_seconds: int = Field(default=120, ge=5, le=600)
    websocket_ping_interval_seconds: int = Field(default=30, ge=5, le=300)
    websocket_ping_timeout_seconds: int = Field(default=180, ge=30, le=900)
    max_retries: int = Field(default=3, ge=0, le=10)
    command_timeout_seconds: int = Field(default=120, ge=1, le=1800)
    maximum_output_bytes: int = Field(default=200_000, ge=1_000)
    job_lease_seconds: int = Field(default=180, ge=10, le=900)
    job_assignment_timeout_seconds: int = Field(default=180, ge=10, le=1800)
    pairing_code_lifetime_seconds: int = Field(default=1800, ge=60, le=86_400)
    worker_credential_lifetime_seconds: int = Field(default=43_200, ge=600, le=604_800)
    remote_probe_interval_seconds: int = Field(default=20, ge=5, le=300)
    remote_failure_threshold: int = Field(default=4, ge=2, le=20)
    remote_recovery_attempts: int = Field(default=6, ge=1, le=20)
    hf_token: str = ""
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    llm_model: str = "code-model"
    llm_timeout_seconds: int = Field(default=180, ge=10, le=1800)
    llm_max_output_tokens: int = Field(default=16_384, ge=512, le=131_072)
    llm_structured_output_mode: str = Field(
        default="auto", pattern="^(auto|json_schema|json_object|prompt)$"
    )
    llm_output_retries: int = Field(default=2, ge=0, le=5)
    llm_context_characters: int = Field(default=60_000, ge=4_000, le=500_000)

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def model_mode(self) -> str:
        return "demo" if self.demo_mode else "model"


@lru_cache
def get_settings() -> Settings:
    return Settings()
