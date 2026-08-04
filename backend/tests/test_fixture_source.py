from datetime import date, datetime
from zoneinfo import ZoneInfo

from a_share_radar.config import Settings
from a_share_radar.data_sources.fixture_source import FixtureSource
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
    assert len(minute_bars) == 61
    assert all(bar.period == "1m" and bar.adjustment == "none" for bar in minute_bars)
    assert minute_bars[0].bar_time == datetime(2026, 8, 4, 9, 30, tzinfo=TZ)
    assert minute_bars[-1].bar_time == datetime(2026, 8, 4, 10, 30, tzinfo=TZ)


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
