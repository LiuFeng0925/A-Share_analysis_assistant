import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from a_share_radar.config import Settings
from a_share_radar.domain.models import Bar, Market, QualityStatus, QuoteSnapshot, Stock
from a_share_radar.main import create_app
from a_share_radar.storage.database import Database
from a_share_radar.storage.repository import MarketRepository

TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DailyRequest:
    code: str
    start: date
    end: date
    period: str
    adjustment: str


@dataclass(frozen=True)
class MinuteRequest:
    code: str
    start: datetime
    end: datetime
    period: str
    adjustment: str


class FakeSource:
    def __init__(self):
        captured = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)
        self.stock_rows = [
            Stock("600519", Market.SH, "贵州茅台"),
            Stock("000001", Market.SZ, "平安银行"),
        ]
        self.snapshot_rows = [
            QuoteSnapshot(
                "600519",
                Market.SH,
                "贵州茅台",
                captured,
                1330.06,
                -2.13,
                -28.92,
                1350.06,
                1350.94,
                1330.04,
                1358.98,
                33455,
                4472998836.0,
                0.27,
                1670000000000.0,
                "fixture",
                QualityStatus.OK,
            ),
            QuoteSnapshot(
                "000001",
                Market.SZ,
                "平安银行",
                captured,
                12.50,
                0.40,
                0.05,
                12.45,
                12.56,
                12.40,
                12.45,
                100000,
                125000000.0,
                0.50,
                242000000000.0,
                "fixture",
                QualityStatus.OK,
            ),
        ]
        self.bar_rows = [
            Bar(
                "600519",
                Market.SH,
                "1m",
                "none",
                captured,
                1334.20,
                1335.08,
                1330.04,
                1330.06,
                1820,
                24250000.0,
                "fixture",
                True,
            ),
            Bar(
                "600519",
                Market.SH,
                "1d",
                "qfq",
                datetime(2026, 8, 4, 15, 0, tzinfo=TZ),
                1350.06,
                1350.94,
                1330.04,
                1330.06,
                33455,
                4472998836.0,
                "fixture",
                True,
            ),
        ]
        self.trading_day_requests: list[tuple[date, date]] = []
        self.daily_requests: list[DailyRequest] = []
        self.minute_requests: list[MinuteRequest] = []
        self.snapshot_requests = 0
        self.snapshot_failures = 0
        self.stock_error: Exception | None = None
        self.trading_day_error: Exception | None = None
        self.snapshot_started: asyncio.Event | None = None
        self.snapshot_release: asyncio.Event | None = None
        self.minute_error: Exception | None = None

    async def fetch_stock_master(self):
        if self.stock_error is not None:
            raise self.stock_error
        return list(self.stock_rows)

    async def fetch_trading_days(self, start, end):
        self.trading_day_requests.append((start, end))
        if self.trading_day_error is not None:
            raise self.trading_day_error
        return {date(2026, 8, 4)}

    async def fetch_market_snapshot(self):
        self.snapshot_requests += 1
        if self.snapshot_failures > 0:
            self.snapshot_failures -= 1
            raise RuntimeError("模拟上游瞬时失败")
        if self.snapshot_started is not None:
            self.snapshot_started.set()
        if self.snapshot_release is not None:
            await self.snapshot_release.wait()
        return list(self.snapshot_rows)

    async def fetch_daily_bars(self, code, start, end, period, adjustment):
        self.daily_requests.append(DailyRequest(code, start, end, period, adjustment))
        return [bar for bar in self.bar_rows if bar.code == code and bar.period == period]

    async def fetch_minute_bars(self, code, start, end, period, adjustment):
        self.minute_requests.append(MinuteRequest(code, start, end, period, adjustment))
        if self.minute_error is not None:
            raise self.minute_error
        return [bar for bar in self.bar_rows if bar.code == code and bar.period == period]


@pytest.fixture
def repository(tmp_path: Path) -> MarketRepository:
    database = Database(tmp_path / "test.duckdb")
    yield MarketRepository(database)
    database.close()


@pytest.fixture
def fake_source():
    return FakeSource()


@pytest.fixture
def app_with_fixture_data(tmp_path, fake_source):
    settings = Settings(data_dir=tmp_path, fixture_source=True)
    database = Database(settings.database_path)
    repository = MarketRepository(database)
    repository.upsert_stocks(fake_source.stock_rows)
    repository.save_snapshot(fake_source.snapshot_rows)
    repository.upsert_bars(fake_source.bar_rows)
    app = create_app(
        settings=settings,
        source=fake_source,
        database=database,
        now_provider=lambda: datetime(2026, 8, 4, 10, 31, tzinfo=TZ),
    )
    yield app
    database.close()
