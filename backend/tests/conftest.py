from pathlib import Path

import pytest

from a_share_radar.storage.database import Database
from a_share_radar.storage.repository import MarketRepository


@pytest.fixture
def repository(tmp_path: Path) -> MarketRepository:
    database = Database(tmp_path / "test.duckdb")
    yield MarketRepository(database)
    database.close()
