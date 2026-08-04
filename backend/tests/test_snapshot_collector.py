from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import a_share_radar.main as main_module
from a_share_radar.services.market_clock import MarketClock
from a_share_radar.services.scheduler import create_scheduler
from a_share_radar.services.snapshot_collector import SnapshotCollector, SnapshotValidationError

TZ = ZoneInfo("Asia/Shanghai")


def test_market_clock_obeys_trading_days_and_session_boundaries():
    trading_day = datetime(2026, 8, 4, tzinfo=TZ).date()
    clock = MarketClock(trading_days={trading_day})

    assert clock.is_open(datetime(2026, 8, 4, 9, 29, tzinfo=TZ)) is False
    assert clock.is_open(datetime(2026, 8, 4, 9, 30, tzinfo=TZ)) is True
    assert clock.is_open(datetime(2026, 8, 4, 11, 30, tzinfo=TZ)) is True
    assert clock.is_open(datetime(2026, 8, 4, 12, 0, tzinfo=TZ)) is False
    assert clock.is_open(datetime(2026, 8, 4, 13, 0, tzinfo=TZ)) is True
    assert clock.is_open(datetime(2026, 8, 4, 15, 0, tzinfo=TZ)) is True
    assert clock.is_open(datetime(2026, 8, 4, 15, 1, tzinfo=TZ)) is False
    assert clock.is_open(datetime(2026, 8, 8, 10, 0, tzinfo=TZ)) is False


async def test_collector_rejects_truncated_batch_without_overwriting(repository, fake_source):
    fake_source.snapshot_rows = fake_source.snapshot_rows[:1]
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)

    with pytest.raises(SnapshotValidationError, match="行情数量异常"):
        await collector.collect_once(datetime(2026, 8, 4, 10, 31, tzinfo=TZ))

    assert repository.snapshot_count() == 0


async def test_collector_rejects_duplicate_stock_without_overwriting(repository, fake_source):
    fake_source.snapshot_rows[1] = fake_source.snapshot_rows[0]
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)

    with pytest.raises(SnapshotValidationError, match="重复股票代码"):
        await collector.collect_once(datetime(2026, 8, 4, 10, 31, tzinfo=TZ))

    assert repository.snapshot_count() == 0


async def test_collector_retries_transient_source_errors_exactly(repository, fake_source):
    fake_source.snapshot_failures = 2
    collector = SnapshotCollector(
        fake_source,
        repository,
        minimum_expected_count=2,
        retry_delays=(0, 0),
    )

    result = await collector.collect_once(datetime(2026, 8, 4, 10, 31, tzinfo=TZ))

    assert result.row_count == 2
    assert fake_source.snapshot_requests == 3


class RecordingClock:
    def __init__(self, is_open: bool):
        self.open = is_open
        self.checks = 0

    def is_open(self, at: datetime) -> bool:
        self.checks += 1
        return self.open


class RecordingCollector:
    def __init__(self):
        self.calls = 0

    async def collect_once(self, at: datetime | None = None):
        self.calls += 1


async def _do_nothing() -> None:
    return None


async def test_scheduler_collects_only_when_market_is_open():
    closed_clock = RecordingClock(False)
    closed_collector = RecordingCollector()
    closed_scheduler = create_scheduler(closed_clock, closed_collector, _do_nothing)
    closed_job = next(job for job in closed_scheduler.get_jobs() if job.func.__name__ == "collect_if_open")

    await closed_job.func()

    assert closed_clock.checks == 1
    assert closed_collector.calls == 0

    open_clock = RecordingClock(True)
    open_collector = RecordingCollector()
    open_scheduler = create_scheduler(open_clock, open_collector, _do_nothing)
    open_job = next(job for job in open_scheduler.get_jobs() if job.func.__name__ == "collect_if_open")

    await open_job.func()

    assert open_clock.checks == 1
    assert open_collector.calls == 1


async def test_lifespan_uses_shanghai_date_for_trading_day_window(
    monkeypatch, repository, fake_source
):
    fixed_now = datetime(2030, 1, 2, 0, 30, tzinfo=TZ)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == TZ
            return fixed_now

    monkeypatch.setattr(main_module, "datetime", FixedDateTime)
    app = main_module.create_app(source=fake_source, database=repository.database)

    async with app.router.lifespan_context(app):
        pass

    assert fake_source.trading_day_requests == [
        (fixed_now.date() - timedelta(days=370), fixed_now.date() + timedelta(days=370))
    ]
