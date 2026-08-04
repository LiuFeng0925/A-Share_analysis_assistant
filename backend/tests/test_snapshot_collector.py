import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import duckdb
import pytest

import a_share_radar.main as main_module
import a_share_radar.services.scheduler as scheduler_module
import a_share_radar.services.snapshot_collector as collector_module
from a_share_radar.config import Settings
from a_share_radar.domain.models import Market, Stock
from a_share_radar.services.market_clock import MarketClock
from a_share_radar.services.scheduler import create_scheduler
from a_share_radar.services.snapshot_collector import SnapshotCollector, SnapshotValidationError

TZ = ZoneInfo("Asia/Shanghai")


def test_market_clock_obeys_trading_days_and_session_boundaries():
    trading_day = datetime(2026, 8, 4, tzinfo=TZ).date()
    clock = MarketClock(trading_days={trading_day})

    assert clock.is_open(datetime(2026, 8, 4, 9, 29, tzinfo=TZ)) is False
    assert clock.is_open(datetime(2026, 8, 4, 9, 30, tzinfo=TZ)) is True
    assert clock.is_open(datetime(2026, 8, 4, 11, 30, 5, tzinfo=TZ)) is True
    assert clock.is_open(datetime(2026, 8, 4, 11, 31, tzinfo=TZ)) is False
    assert clock.is_open(datetime(2026, 8, 4, 12, 0, tzinfo=TZ)) is False
    assert clock.is_open(datetime(2026, 8, 4, 13, 0, tzinfo=TZ)) is True
    assert clock.is_open(datetime(2026, 8, 4, 15, 0, 5, tzinfo=TZ)) is True
    assert clock.is_open(datetime(2026, 8, 4, 15, 1, tzinfo=TZ)) is False
    assert clock.is_open(datetime(2026, 8, 8, 10, 0, tzinfo=TZ)) is False


def test_market_clock_converts_aware_datetime_to_shanghai_before_checking():
    trading_day = datetime(2026, 8, 4, tzinfo=TZ).date()
    clock = MarketClock(trading_days={trading_day})

    assert clock.is_open(datetime(2026, 8, 4, 2, 0, tzinfo=UTC)) is True
    assert clock.is_open(
        datetime(2026, 8, 3, 21, 30, tzinfo=timezone(timedelta(hours=-4)))
    ) is True


def test_market_clock_rejects_naive_datetime():
    trading_day = datetime(2026, 8, 4, tzinfo=TZ).date()
    clock = MarketClock(trading_days={trading_day})

    with pytest.raises(ValueError, match="时区"):
        clock.is_open(datetime(2026, 8, 4, 10, 0, tzinfo=TZ).replace(tzinfo=None))


async def test_collector_rejects_truncated_batch_without_overwriting(repository, fake_source):
    fake_source.snapshot_rows = fake_source.snapshot_rows[:1]
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)

    with pytest.raises(SnapshotValidationError, match="行情数量异常"):
        await collector.collect_once(datetime(2026, 8, 4, 10, 31, tzinfo=TZ))

    assert repository.snapshot_count() == 0
    assert fake_source.snapshot_requests == 1


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


async def test_collector_does_not_retry_storage_errors(monkeypatch, repository, fake_source):
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)

    def fail_storage(quotes):
        raise RuntimeError("模拟存储失败")

    monkeypatch.setattr(repository, "save_snapshot", fail_storage)

    with pytest.raises(RuntimeError, match="模拟存储失败"):
        await collector.collect_once(datetime(2026, 8, 4, 10, 31, tzinfo=TZ))

    assert fake_source.snapshot_requests == 1


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


def set_fixed_now(monkeypatch, fixed_now: datetime) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == TZ
            return fixed_now

    monkeypatch.setattr(main_module, "datetime", FixedDateTime)
    monkeypatch.setattr(scheduler_module, "datetime", FixedDateTime)


def assert_app_database_is_closed(app) -> None:
    with pytest.raises(duckdb.ConnectionException):
        app.state.repository.database.connection.execute("SELECT 1")


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
    set_fixed_now(monkeypatch, fixed_now)
    app = main_module.create_app(source=fake_source, database=repository.database)

    async with app.router.lifespan_context(app):
        pass

    assert fake_source.trading_day_requests == [
        (fixed_now.date() - timedelta(days=370), fixed_now.date() + timedelta(days=370))
    ]
    repository.database.connection.execute("SELECT 1")


def test_create_app_defers_default_database_until_lifespan(tmp_path, fake_source):
    settings = Settings(data_dir=tmp_path / "延迟数据库")

    app = main_module.create_app(settings=settings, source=fake_source)

    assert app.state.repository is None
    assert settings.database_path.exists() is False


async def test_lifespan_preserves_local_stocks_when_stock_source_fails(
    monkeypatch, caplog, repository, fake_source
):
    repository.upsert_stocks([Stock("600519", Market.SH, "本地贵州茅台")])
    fake_source.stock_error = RuntimeError("模拟股票主数据失败")
    set_fixed_now(monkeypatch, datetime(2026, 8, 4, 12, 0, tzinfo=TZ))
    app = main_module.create_app(source=fake_source, database=repository.database)

    with caplog.at_level(logging.ERROR):
        async with app.router.lifespan_context(app):
            assert app.state.repository.list_all_stocks() == [
                Stock("600519", Market.SH, "本地贵州茅台")
            ]

    assert "加载股票主数据失败，继续使用本地已有数据" in caplog.text


async def test_lifespan_treats_trading_day_failure_as_closed(
    monkeypatch, caplog, repository, fake_source
):
    fake_source.trading_day_error = RuntimeError("模拟交易日失败")
    set_fixed_now(monkeypatch, datetime(2026, 8, 4, 10, 0, tzinfo=TZ))
    app = main_module.create_app(source=fake_source, database=repository.database)

    with caplog.at_level(logging.ERROR):
        async with app.router.lifespan_context(app):
            assert app.state.clock.trading_days == set()

    assert fake_source.snapshot_requests == 0
    assert "加载交易日失败，本轮按闭市处理" in caplog.text


async def test_lifespan_continues_when_initial_snapshot_fails(
    monkeypatch, caplog, repository, fake_source
):
    async def skip_retry_delay(delay):
        return None

    fake_source.snapshot_failures = 3
    monkeypatch.setattr(collector_module.asyncio, "sleep", skip_retry_delay)
    set_fixed_now(monkeypatch, datetime(2026, 8, 4, 10, 0, tzinfo=TZ))
    app = main_module.create_app(source=fake_source, database=repository.database)

    with caplog.at_level(logging.ERROR):
        async with app.router.lifespan_context(app):
            assert app.state.scheduler.running is True

    assert fake_source.snapshot_requests == 3
    assert "首次全市场行情采集失败，继续使用本地已有数据" in caplog.text


async def test_lifespan_skips_initial_snapshot_during_lunch_break(
    monkeypatch, repository, fake_source
):
    set_fixed_now(monkeypatch, datetime(2026, 8, 4, 12, 0, tzinfo=TZ))
    app = main_module.create_app(source=fake_source, database=repository.database)

    async with app.router.lifespan_context(app):
        assert app.state.scheduler is not None

    assert fake_source.snapshot_requests == 0


async def test_default_database_closes_when_scheduler_creation_fails(
    monkeypatch, tmp_path, fake_source
):
    def fail_scheduler_creation(clock, collector, archive_callback):
        raise RuntimeError("模拟调度器创建失败")

    set_fixed_now(monkeypatch, datetime(2026, 8, 4, 12, 0, tzinfo=TZ))
    monkeypatch.setattr(main_module, "create_scheduler", fail_scheduler_creation)
    settings = Settings(data_dir=tmp_path / "创建失败")
    app = main_module.create_app(settings=settings, source=fake_source)

    with pytest.raises(RuntimeError, match="模拟调度器创建失败"):
        async with app.router.lifespan_context(app):
            pass

    assert_app_database_is_closed(app)


async def test_default_database_closes_when_scheduler_start_fails(
    monkeypatch, tmp_path, fake_source
):
    class StartFailureScheduler:
        running = False

        def start(self):
            raise RuntimeError("模拟调度器启动失败")

    set_fixed_now(monkeypatch, datetime(2026, 8, 4, 12, 0, tzinfo=TZ))
    monkeypatch.setattr(
        main_module,
        "create_scheduler",
        lambda clock, collector, archive_callback: StartFailureScheduler(),
    )
    settings = Settings(data_dir=tmp_path / "启动失败")
    app = main_module.create_app(settings=settings, source=fake_source)

    with pytest.raises(RuntimeError, match="模拟调度器启动失败"):
        async with app.router.lifespan_context(app):
            pass

    assert app.state.scheduler is not None
    assert_app_database_is_closed(app)


async def test_lifespan_waits_for_running_collection_before_closing_default_database(
    monkeypatch, tmp_path, fake_source
):
    fixed_now = datetime(2026, 8, 4, 10, 0, tzinfo=TZ)
    set_fixed_now(monkeypatch, fixed_now)
    settings = Settings(data_dir=tmp_path / "安全关闭")
    app = main_module.create_app(settings=settings, source=fake_source)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    collection_job = next(
        job for job in app.state.scheduler.get_jobs() if job.func.__name__ == "collect_if_open"
    )
    fake_source.snapshot_started = asyncio.Event()
    fake_source.snapshot_release = asyncio.Event()
    running_collection = asyncio.create_task(collection_job.func())
    await fake_source.snapshot_started.wait()

    shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
    try:
        await asyncio.sleep(0)
        assert shutdown.done() is False
        app.state.repository.database.connection.execute("SELECT 1")
    finally:
        fake_source.snapshot_release.set()
        await running_collection
        await shutdown

    assert app.state.scheduler.running is False
    assert_app_database_is_closed(app)
