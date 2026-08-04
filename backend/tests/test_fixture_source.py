from datetime import date, datetime
from zoneinfo import ZoneInfo

from httpx import ASGITransport, AsyncClient

from a_share_radar.config import Settings
from a_share_radar.data_sources.fixture_source import FixtureSource
from a_share_radar.domain.models import BarFetchBatch, QualityStatus
from a_share_radar.main import create_app

TZ = ZoneInfo("Asia/Shanghai")


async def test_fixture_source_returns_deterministic_complete_mvp_data():
    source = FixtureSource()

    stocks = await source.fetch_stock_master()
    trading_days = await source.fetch_trading_days(
        date(2026, 8, 1), date(2026, 8, 6)
    )
    snapshots = await source.fetch_market_snapshot()
    daily_bars = await source.fetch_daily_bars(
        "600519", date(2026, 5, 1), date(2026, 8, 4), "1d", "qfq"
    )
    minute_bars = await source.fetch_minute_bars(
        "600519",
        datetime(2026, 8, 4, 9, 30, tzinfo=TZ),
        datetime(2026, 8, 4, 15, 0, tzinfo=TZ),
        "1m",
        "none",
    )

    assert [(stock.code, stock.name) for stock in stocks] == [
        ("600519", "贵州茅台"),
        ("000001", "平安银行"),
    ]
    assert trading_days == {date(2026, 8, 3), date(2026, 8, 4)}
    assert len(snapshots) == 2
    assert all(snapshot.source == "fixture" for snapshot in snapshots)
    assert len(daily_bars) == 60
    assert all(bar.period == "1d" and bar.adjustment == "qfq" for bar in daily_bars)
    assert daily_bars[-1].bar_time == datetime(2026, 8, 3, 15, 0, tzinfo=TZ)
    assert daily_bars[-1].acquired_at == source.captured_at
    assert len(minute_bars) == 61
    assert all(bar.period == "1m" and bar.adjustment == "none" for bar in minute_bars)
    assert minute_bars[0].bar_time == datetime(2026, 8, 4, 9, 30, tzinfo=TZ)
    assert minute_bars[-1].bar_time == datetime(2026, 8, 4, 10, 30, tzinfo=TZ)
    assert all(bar.acquired_at == source.captured_at for bar in minute_bars)


async def test_fixture_source_filters_unknown_stock_and_out_of_range_bars():
    source = FixtureSource()

    assert (
        await source.fetch_daily_bars(
            "999999", date(2026, 1, 1), date(2026, 8, 4), "1d", "qfq"
        )
        == []
    )
    assert (
        await source.fetch_minute_bars(
            "600519",
            datetime(2026, 8, 5, 9, 30, tzinfo=TZ),
            datetime(2026, 8, 5, 15, 0, tzinfo=TZ),
            "1m",
            "none",
        )
        == []
    )


async def test_fixture_lifespan_persists_all_data_without_background_jobs(tmp_path):
    app = create_app(settings=Settings(data_dir=tmp_path, fixture_source=True))

    async with app.router.lifespan_context(app):
        status = app.state.repository.data_status()
        assert status.stock_count == 2
        assert status.latest_quote_count == 2
        assert status.snapshot_count == 2
        assert status.bar_count == 242
        assert isinstance(app.state.source, FixtureSource)
        assert app.state.scheduler is None
        assert app.state.history_task is None


async def test_fixture_lifespan_accepts_bar_fetch_batches(tmp_path):
    delegate = FixtureSource()

    class BatchFixtureSource:
        fetch_stock_master = delegate.fetch_stock_master
        fetch_trading_days = delegate.fetch_trading_days
        fetch_market_snapshot = delegate.fetch_market_snapshot

        async def fetch_daily_bars(self, *args):
            bars = await delegate.fetch_daily_bars(*args)
            return BarFetchBatch(
                tuple(bars),
                delegate.captured_at,
                "fixture",
                QualityStatus.OK,
                len(bars),
                0,
            )

        async def fetch_minute_bars(self, *args):
            bars = await delegate.fetch_minute_bars(*args)
            return BarFetchBatch(
                tuple(bars),
                delegate.captured_at,
                "fixture",
                QualityStatus.OK,
                len(bars),
                0,
            )

    app = create_app(
        settings=Settings(data_dir=tmp_path, fixture_source=True),
        source=BatchFixtureSource(),
    )

    async with app.router.lifespan_context(app):
        assert app.state.repository.data_status().bar_count == 242


async def test_fixture_api_ignores_cross_date_wall_clock_and_stays_deterministic(
    tmp_path,
):
    crossed_wall_clock = lambda: datetime(2030, 1, 2, 10, 30, tzinfo=TZ)
    app = create_app(
        settings=Settings(data_dir=tmp_path, fixture_source=True),
        now_provider=crossed_wall_clock,
    )

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            summary_response = await client.get("/api/market/summary")
            bars_response = await client.get(
                "/api/stocks/SH/600519/bars",
                params={"period": "1m", "range": "today", "adjustment": "none"},
            )

        assert app.state.now_provider() == FixtureSource.captured_at
        assert summary_response.status_code == 200
        assert summary_response.json()["market_status"] == "open"
        assert summary_response.json()["stale"] is False
        assert bars_response.status_code == 200
        assert len(bars_response.json()["items"]) == 61
        assert bars_response.json()["items"][0]["bar_time"].startswith("2026-08-04")
        assert bars_response.json()["items"][-1]["bar_time"].startswith("2026-08-04")


async def test_fixture_lifespan_derives_now_from_snapshot_without_source_clock(
    tmp_path,
):
    delegate = FixtureSource()

    class FixtureWithoutClock:
        fetch_stock_master = delegate.fetch_stock_master
        fetch_trading_days = delegate.fetch_trading_days
        fetch_market_snapshot = delegate.fetch_market_snapshot
        fetch_daily_bars = delegate.fetch_daily_bars
        fetch_minute_bars = delegate.fetch_minute_bars

    app = create_app(
        settings=Settings(data_dir=tmp_path, fixture_source=True),
        source=FixtureWithoutClock(),
        now_provider=lambda: datetime(2030, 1, 2, 10, 30, tzinfo=TZ),
    )

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/stocks/SH/600519/bars",
                params={"period": "1m", "range": "today", "adjustment": "none"},
            )

        assert app.state.now_provider() == FixtureSource.captured_at
        assert response.status_code == 200
        assert len(response.json()["items"]) == 61
