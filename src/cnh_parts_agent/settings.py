"""Host settings. Credentials remain SDK-owned and never enter tool arguments."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PARTS_", extra="forbid")
    model: str = "gpt-5.6-luna"
    cache_path: Path = Path(".cache/catalog.sqlite")
    index_dir: Path | None = None
    user_agent: str = "cnh-parts-agent/0.1 (local research proof of concept)"
    max_calls: int = Field(default=8, ge=1, le=8)
    timeout_seconds: float = Field(default=30, gt=0, le=30)
