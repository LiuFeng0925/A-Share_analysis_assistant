import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

import a_share_radar.main as main_module
from a_share_radar.domain.models import Bar, Market, QualityStatus, Stock
from a_share_radar.services.bar_service import BarService, HistoryBootstrapper
from a_share_radar.services.scheduler import create_scheduler
from a_share_radar.storage.database import Database
from a_share_radar.storage.repository import MarketRepository

TZ = ZoneInfo("Asia/Shanghai")


def _daily_bar(day: date, *, complete: bool = True) -> Bar:
    return Bar(
        code="600519",
        market=Market.SH,
        period="1d",
        adjustment="qfq",
        bar_time=datetime(day.year, day.month, day.day, 15, 0, tzinfo=TZ),
        open_price=10.0,
        high_price=10.5,
        low_price=9.8,
        close_price=10.2,
        volume=100_000,
        amount=1_020_000.0,
        source="fixture",
        is_complete=complete,
        acquired_at=datetime(day.year, day.month, day.day, 15, 10, tzinfo=TZ),
        quality_status=QualityStatus.OK if complete else QualityStatus.PARTIAL,
    )


def test_bar_storage_round_trips_acquisition_time_and_quality(repository):
    bar = _daily_bar(date(2026, 8, 3))

    repository.upsert_bars([bar])
    saved = repository.get_bars(
        Market.SH,
        "600519",
        "1d",
        datetime(2026, 8, 3, tzinfo=TZ),
        datetime(2026, 8, 3, 23, 59, tzinfo=TZ),
        "qfq",
    )

    assert saved == [bar]


def test_database_migrates_legacy_bar_rows(tmp_path: Path):
    path = tmp_path / "legacy.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE bar_hot (
          market VARCHAR NOT NULL, code VARCHAR NOT NULL, period VARCHAR NOT NULL,
          adjustment VARCHAR NOT NULL, bar_time TIMESTAMPTZ NOT NULL,
          open_price DOUBLE NOT NULL, high_price DOUBLE NOT NULL,
          low_price DOUBLE NOT NULL, close_price DOUBLE NOT NULL,
          volume BIGINT NOT NULL, amount DOUBLE NOT NULL, source VARCHAR NOT NULL,
          is_complete BOOLEAN NOT NULL,
          PRIMARY KEY (market, code, period, adjustment, bar_time)
        )
        """
    )
    bar_time = datetime(2026, 8, 3, 15, 0, tzinfo=TZ)
    connection.execute(
        "INSERT INTO bar_hot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["SH", "600519", "1d", "qfq", bar_time, 10, 11, 9, 10.5, 100, 1050, "legacy", True],
    )
    connection.close()

    database = Database(path)
    try:
        bars = MarketRepository(database).get_bars(
            Market.SH,
            "600519",
            "1d",
            datetime(2026, 8, 3, tzinfo=TZ),
            datetime(2026, 8, 3, 23, 59, tzinfo=TZ),
            "qfq",
        )
    finally:
        database.close()

    assert bars[0].acquired_at == bar_time
    assert bars[0].quality_status is QualityStatus.OK


async def test_daily_restart_fetches_only_gap(repository, fake_source):
    repository.upsert_bars([_daily_bar(date(2026, 8, 3))])
    fake_source.bar_rows = [_daily_bar(date(2026, 8, 4))]
    service = BarService(fake_source, repository, history_days=60)

    await service.ensure_daily_history(Stock("600519", Market.SH, "贵州茅台"), date(2026, 8, 4))

    assert len(fake_source.daily_requests) == 1
    assert fake_source.daily_requests[0].start == date(2026, 8, 4)


async def test_daily_restart_skips_up_to_date_stock(repository, fake_source):
    repository.upsert_bars([_daily_bar(date(2026, 8, 4))])
    service = BarService(fake_source, repository, history_days=60)

    bars = await service.ensure_daily_history(
        Stock("600519", Market.SH, "贵州茅台"), date(2026, 8, 4)
    )

    assert fake_source.daily_requests == []
    assert bars[-1].bar_time.date() == date(2026, 8, 4)


async def test_daily_query_refreshes_stale_tail(repository, fake_source):
    repository.upsert_bars([_daily_bar(date(2026, 8, 3))])
    fake_source.bar_rows = [_daily_bar(date(2026, 8, 4))]
    service = BarService(fake_source, repository, history_days=60)

    bars = await service.get_bars(
        Market.SH,
        "600519",
        "1d",
        "5d",
        "qfq",
        datetime(2026, 8, 4, 15, 10, tzinfo=TZ),
    )

    assert fake_source.daily_requests[0].start == date(2026, 8, 4)
    assert bars[-1].bar_time.date() == date(2026, 8, 4)


async def test_daily_refresh_failure_returns_existing_cache(
    repository, fake_source, monkeypatch
):
    cached = _daily_bar(date(2026, 8, 3))
    repository.upsert_bars([cached])

    async def fail(*args):
        raise RuntimeError("模拟日 K 上游失败")

    monkeypatch.setattr(fake_source, "fetch_daily_bars", fail)
    service = BarService(fake_source, repository, history_days=60)

    bars = await service.get_bars(
        Market.SH,
        "600519",
        "1d",
        "5d",
        "qfq",
        datetime(2026, 8, 4, 15, 10, tzinfo=TZ),
    )

    assert bars == [cached]


async def test_service_preserves_partial_quality_reported_by_source(
    repository, fake_source
):
    partial = replace(
        _daily_bar(date(2026, 8, 3)), quality_status=QualityStatus.PARTIAL
    )
    fake_source.bar_rows = [partial]
    service = BarService(fake_source, repository, history_days=60)

    bars = await service.get_bars(
        Market.SH,
        "600519",
        "1d",
        "5d",
        "qfq",
        datetime(2026, 8, 4, 15, 10, tzinfo=TZ),
    )

    assert bars[-1].is_complete is True
    assert bars[-1].quality_status is QualityStatus.PARTIAL


async def test_daily_query_marks_pre_close_today_as_incomplete(repository, fake_source):
    fake_source.bar_rows = [_daily_bar(date(2026, 8, 4))]
    service = BarService(fake_source, repository, history_days=60)

    bars = await service.get_bars(
        Market.SH,
        "600519",
        "1d",
        "5d",
        "qfq",
        datetime(2026, 8, 4, 14, 0, tzinfo=TZ),
    )

    assert bars[-1].is_complete is False
    assert bars[-1].quality_status is QualityStatus.PARTIAL


async def test_incomplete_daily_tail_is_refreshed_after_ttl(repository, fake_source):
    fake_source.bar_rows = [_daily_bar(date(2026, 8, 4))]
    service = BarService(fake_source, repository, history_days=60, query_ttl_seconds=0)
    first_now = datetime(2026, 8, 4, 14, 0, tzinfo=TZ)

    await service.get_bars(Market.SH, "600519", "1d", "5d", "qfq", first_now)
    await service.get_bars(
        Market.SH,
        "600519",
        "1d",
        "5d",
        "qfq",
        first_now + timedelta(minutes=1),
    )

    assert len(fake_source.daily_requests) == 2
    assert fake_source.daily_requests[-1].start == date(2026, 8, 4)


@pytest.mark.parametrize(("period", "overlap_days"), [("1w", 7), ("1mo", 31)])
async def test_week_and_month_tail_refresh_only_uses_bounded_overlap(
    repository, fake_source, period, overlap_days
):
    now = datetime(2026, 8, 4, 14, 0, tzinfo=TZ)
    cached = replace(
        _daily_bar(now.date(), complete=False),
        period=period,
        acquired_at=now - timedelta(minutes=5),
    )
    fake_source.bar_rows = [cached]
    repository.upsert_bars([cached])
    service = BarService(fake_source, repository, history_days=60, query_ttl_seconds=0)

    await service.get_bars(Market.SH, "600519", period, "60d", "qfq", now)

    request = fake_source.daily_requests[0]
    assert request.start == cached.bar_time.date() - timedelta(days=overlap_days)
    assert request.end == now.date()


async def test_same_query_uses_one_shared_fetch(repository, fake_source, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fetch(code, start, end, period, adjustment):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return [fake_source.bar_rows[0]]

    monkeypatch.setattr(fake_source, "fetch_minute_bars", fetch)
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)
    first = asyncio.create_task(service.get_bars(Market.SH, "600519", "1m", "today", "none", now))
    await started.wait()
    second = asyncio.create_task(service.get_bars(Market.SH, "600519", "1m", "today", "none", now))
    await asyncio.sleep(0)

    assert calls == 1
    release.set()
    await asyncio.gather(first, second)
    assert service.inflight_count == 0


async def test_cancelling_one_waiter_does_not_cancel_shared_fetch(
    repository, fake_source, monkeypatch
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch(code, start, end, period, adjustment):
        started.set()
        await release.wait()
        return [fake_source.bar_rows[0]]

    monkeypatch.setattr(fake_source, "fetch_minute_bars", fetch)
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)
    cancelled = asyncio.create_task(
        service.get_bars(Market.SH, "600519", "1m", "today", "none", now)
    )
    await started.wait()
    survivor = asyncio.create_task(
        service.get_bars(Market.SH, "600519", "1m", "today", "none", now)
    )

    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()

    assert await survivor
    assert service.inflight_count == 0


async def test_recent_query_result_uses_short_ttl(repository, fake_source):
    service = BarService(fake_source, repository, history_days=60, query_ttl_seconds=5)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)

    first = await service.get_bars(Market.SH, "600519", "1m", "today", "none", now)
    second = await service.get_bars(
        Market.SH, "600519", "1m", "today", "none", now + timedelta(seconds=1)
    )

    assert first == second
    assert len(fake_source.minute_requests) == 1


async def test_minute_query_fetches_with_one_bar_overlap(repository, fake_source):
    cached = replace(
        fake_source.bar_rows[0],
        bar_time=datetime(2026, 8, 4, 10, 30, tzinfo=TZ),
        acquired_at=datetime(2026, 8, 4, 10, 30, 30, tzinfo=TZ),
    )
    repository.upsert_bars([cached])
    service = BarService(fake_source, repository, history_days=60)

    await service.get_bars(
        Market.SH,
        "600519",
        "1m",
        "today",
        "none",
        datetime(2026, 8, 4, 10, 32, tzinfo=TZ),
    )

    assert fake_source.minute_requests[0].start == datetime(2026, 8, 4, 10, 29, tzinfo=TZ)


async def test_history_bootstrapper_resumes_after_failure_on_next_run(
    repository, fake_source, monkeypatch
):
    repository.upsert_stocks(fake_source.stock_rows)
    service = BarService(fake_source, repository, history_days=60)
    failures = {"600519": 1}
    calls: list[str] = []

    async def ensure(stock, end_date):
        calls.append(stock.code)
        if failures.get(stock.code, 0):
            failures[stock.code] -= 1
            raise RuntimeError("模拟失败")
        return []

    monkeypatch.setattr(service, "ensure_daily_history", ensure)
    bootstrapper = HistoryBootstrapper(
        service,
        repository,
        delay_seconds=0,
        now_provider=lambda: datetime(2026, 8, 4, 15, 20, tzinfo=TZ),
    )

    await bootstrapper.run()
    await bootstrapper.run()

    assert calls == ["600519", "000001", "600519", "000001"]


async def test_history_trigger_during_running_batch_is_coalesced_not_lost(
    repository, fake_source, monkeypatch
):
    repository.upsert_stocks(fake_source.stock_rows[:1])
    service = BarService(fake_source, repository, history_days=60)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def ensure(stock, end_date):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return []

    monkeypatch.setattr(service, "ensure_daily_history", ensure)
    bootstrapper = HistoryBootstrapper(
        service,
        repository,
        delay_seconds=0,
        now_provider=lambda: datetime(2026, 8, 4, 15, 20, tzinfo=TZ),
    )
    running = asyncio.create_task(bootstrapper.run())
    await started.wait()

    await bootstrapper.run()
    release.set()
    await running

    assert calls == 2


async def test_scheduler_runs_daily_history_after_close():
    calls = 0

    class Clock:
        def is_open(self, now):
            return False

    class Collector:
        async def collect_once(self, now):
            return None

    async def no_op():
        return None

    async def append_daily_history():
        nonlocal calls
        calls += 1

    scheduler = create_scheduler(
        Clock(),
        Collector(),
        no_op,
        daily_history_callback=append_daily_history,
    )
    job = next(
        item for item in scheduler.get_jobs() if item.func.__name__ == "daily_history_managed"
    )

    await job.func()

    assert calls == 1
    assert "hour='15'" in str(job.trigger)
    assert "minute='20'" in str(job.trigger)


async def test_lifespan_starts_history_after_refreshing_trading_calendar(
    repository, fake_source, monkeypatch
):
    repository.replace_trading_days({date(2026, 8, 1)})
    seen_days: set[date] = set()
    started = asyncio.Event()

    class RecordingBootstrapper:
        def __init__(
            self, bar_service, repository, delay_seconds, now_provider=None
        ):
            self.repository = repository

        async def run(self):
            seen_days.update(self.repository.list_trading_days())
            started.set()

    monkeypatch.setattr(main_module, "HistoryBootstrapper", RecordingBootstrapper)
    app = main_module.create_app(
        source=fake_source,
        database=repository.database,
        now_provider=lambda: datetime(2026, 8, 4, 15, 20, tzinfo=TZ),
    )

    async with app.router.lifespan_context(app):
        await started.wait()

    assert seen_days == {date(2026, 8, 4)}


async def test_service_close_cancels_shared_fetch_and_rejects_new_query(
    repository, fake_source, monkeypatch
):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fetch(code, start, end, period, adjustment):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(fake_source, "fetch_minute_bars", fetch)
    service = BarService(fake_source, repository, history_days=60)
    query = asyncio.create_task(
        service.get_bars(
            Market.SH,
            "600519",
            "1m",
            "today",
            "none",
            datetime(2026, 8, 4, 10, 31, tzinfo=TZ),
        )
    )
    await started.wait()

    await service.close()

    assert cancelled.is_set()
    with pytest.raises(asyncio.CancelledError):
        await query
    with pytest.raises(RuntimeError, match="已关闭"):
        await service.get_bars(
            Market.SH,
            "600519",
            "1m",
            "today",
            "none",
            datetime(2026, 8, 4, 10, 32, tzinfo=TZ),
        )
