from pathlib import Path

from pydantic import computed_field
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

    @computed_field
    @property
    def database_path(self) -> Path:
        return self.data_dir / "a_share_radar.duckdb"
