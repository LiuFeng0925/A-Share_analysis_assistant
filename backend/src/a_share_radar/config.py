from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="A_SHARE_", env_file=".env")

    data_dir: Path = Path("data")
    timezone: str = "Asia/Shanghai"
    snapshot_interval_seconds: int = 60
    stale_after_seconds: int = 120
    history_days: int = 60
    history_request_delay_seconds: float = 0.5
    fixture_source: bool = False
    frontend_port: int = Field(default=5173, ge=1, le=65535)

    @computed_field
    @property
    def database_path(self) -> Path:
        return self.data_dir / "a_share_radar.duckdb"
