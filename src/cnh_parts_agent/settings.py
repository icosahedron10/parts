"""Host settings. Credentials remain SDK-owned and never enter tool arguments."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LOOKUP_TIMEOUT_SECONDS = 180


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PARTS_", extra="forbid")
    model: str = "gpt-5.6-luna"
    cache_path: Path = Path(".cache/catalog.sqlite")
    index_dir: Path | None = None
    user_agent: str = "cnh-parts-agent/0.1 (local research proof of concept)"
    max_calls: int = Field(default=8, ge=1, le=8)
    timeout_seconds: float = Field(default=DEFAULT_LOOKUP_TIMEOUT_SECONDS, gt=0, le=600)
    source_connect_timeout_seconds: float = Field(default=10, gt=0, le=60)
    source_read_timeout_seconds: float = Field(default=30, gt=0, le=120)
    source_operation_timeout_seconds: float = Field(default=60, gt=0, le=300)
    source_max_response_bytes: int = Field(default=32 * 1024 * 1024, ge=1024, le=128 * 1024 * 1024)
    model_timeout_seconds: float = Field(default=60, gt=0, le=180)
    model_max_output_tokens: int = Field(default=8192, ge=256, le=32768)
