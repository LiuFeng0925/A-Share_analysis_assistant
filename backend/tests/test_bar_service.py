import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from threading import Event, Thread, get_ident
from zoneinfo import ZoneInfo

import pytest

from a_share_radar.domain.models import Market, Stock
from a_share_radar.main import create_app
from a_share_radar.services.bar_service import BarService, HistoryBootstrapper

TZ = ZoneInfo("Asia/Shanghai")


def _trading_days_ending(end: date, count: int) -> list[date]:
    days: list[date] = []
    current = end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return sorted(days)


async def test_daily_history_requests_sixty_trading_days(repository, fake_source):
    trading_days = _trading_days_ending(date(2026, 8, 4), 80)
    repository.replace_trading_days(set(trading_days))
    service = BarService(fake_source, repository, history_days=60)

    await service.ensure_daily_history(
        Stock("600519", Market.SH, "贵州茅台"), date(2026, 8, 4)
    )

    request = fake_source.daily_requests[0]
    assert request.code == "600519"
    assert request.end == date(2026, 8, 4)
    assert request.start == trading_days[-60]


async def test_daily_history_only_saves_latest_sixty_bars(repository, fake_source):
    template = fake_source.bar_rows[1]
    trading_days = _trading_days_ending(date(2026, 8, 4), 80)
    repository.replace_trading_days(set(trading_days))
    fake_source.bar_rows = [
        replace(
            template,
            bar_time=datetime(day.year, day.month, day.day, 15, 0, tzinfo=TZ),
        )
        for day in trading_days[-65:]
    ]
    service = BarService(fake_source, repository, history_days=60)

    bars = await service.ensure_daily_history(
        Stock("600519", Market.SH, "贵州茅台"), date(2026, 8, 4)
    )

    assert len(bars) == 60
    saved = repository.get_bars(
        Market.SH,
        "600519",
        "1d",
        datetime(1990, 1, 1, tzinfo=TZ),
        datetime(2026, 8, 4, 23, 59, tzinfo=TZ),
        "qfq",
    )
    assert saved == bars


@pytest.mark.parametrize(
    ("period", "range_name", "adjustment", "message"),
    [
        ("2m", "today", "none", "周期"),
        ("1m", "unknown", "none", "时间范围"),
        ("1m", "today", "bad", "复权"),
        ("1d", "today", "none", "今日视图"),
        ("1m", "5d", "qfq", "一分钟 K"),
    ],
)
async def test_get_bars_rejects_invalid_parameter_combinations(
    repository, fake_source, period, range_name, adjustment, message
):
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)

    with pytest.raises(ValueError, match=message):
        await service.get_bars(
            Market.SH, "600519", period, range_name, adjustment, now
        )


async def test_get_bars_rejects_naive_now(repository, fake_source):
    service = BarService(fake_source, repository, history_days=60)

    with pytest.raises(ValueError, match="时区"):
        await service.get_bars(
            Market.SH,
            "600519",
            "1m",
            "today",
            "none",
            datetime(2026, 8, 4, 10, 31, tzinfo=TZ).replace(tzinfo=None),
        )


async def test_today_uses_native_one_minute_source_from_open_to_now(
    repository, fake_source
):
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)

    bars = await service.get_bars(
        Market.SH, "600519", "1m", "today", "none", now
    )

    request = fake_source.minute_requests[0]
    assert request.period == "1m"
    assert request.start == datetime(2026, 8, 4, 9, 30, tzinfo=TZ)
    assert request.end == now
    assert all(bar.period == "1m" for bar in bars)


async def test_today_normalizes_aware_now_to_shanghai(repository, fake_source):
    service = BarService(fake_source, repository, history_days=60)
    utc = ZoneInfo("UTC")

    await service.get_bars(
        Market.SH,
        "600519",
        "1m",
        "today",
        "none",
        datetime(2026, 8, 4, 2, 31, tzinfo=utc),
    )

    request = fake_source.minute_requests[0]
    assert request.start == datetime(2026, 8, 4, 9, 30, tzinfo=TZ)
    assert request.end == datetime(2026, 8, 4, 10, 31, tzinfo=TZ)


async def test_repository_operations_do_not_block_event_loop(
    monkeypatch, repository, fake_source
):
    service = BarService(fake_source, repository, history_days=60)
    loop = asyncio.get_running_loop()
    event_loop_thread = get_ident()
    read_started = Event()
    heartbeat_seen = Event()
    release_read = Event()
    heartbeat_before_release: list[bool] = []
    operation_threads: list[tuple[str, int]] = []
    original_get_bars = repository.get_bars
    original_upsert_bars = repository.upsert_bars
    block_first_read = True

    def controlled_get_bars(*args, **kwargs):
        nonlocal block_first_read
        operation_threads.append(("get", get_ident()))
        if block_first_read:
            block_first_read = False
            read_started.set()
            release_read.wait()
        return original_get_bars(*args, **kwargs)

    def recording_upsert_bars(*args, **kwargs):
        operation_threads.append(("upsert", get_ident()))
        return original_upsert_bars(*args, **kwargs)

    async def heartbeat():
        await asyncio.sleep(0)
        heartbeat_seen.set()

    def control_blocked_read():
        if not read_started.wait(timeout=2):
            heartbeat_before_release.append(False)
            release_read.set()
            return
        loop.call_soon_threadsafe(asyncio.create_task, heartbeat())
        heartbeat_before_release.append(heartbeat_seen.wait(timeout=1))
        release_read.set()

    monkeypatch.setattr(repository, "get_bars", controlled_get_bars)
    monkeypatch.setattr(repository, "upsert_bars", recording_upsert_bars)
    controller = Thread(target=control_blocked_read)
    controller.start()
    try:
        await service.get_bars(
            Market.SH,
            "600519",
            "1m",
            "today",
            "none",
            datetime(2026, 8, 4, 10, 31, tzinfo=TZ),
        )
    finally:
        release_read.set()
        controller.join(timeout=2)

    assert heartbeat_before_release == [True]
    assert [name for name, _ in operation_threads] == ["get", "upsert", "get"]
    assert all(thread_id != event_loop_thread for _, thread_id in operation_threads)


async def test_cancellation_does_not_cancel_shared_repository_thread(
    monkeypatch, repository, fake_source
):
    read_started = Event()
    release_read = Event()
    original_get_bars = repository.get_bars

    def blocking_get_bars(*args, **kwargs):
        read_started.set()
        release_read.wait()
        return original_get_bars(*args, **kwargs)

    monkeypatch.setattr(repository, "get_bars", blocking_get_bars)
    service = BarService(fake_source, repository, history_days=60)
    task = asyncio.create_task(
        service.get_bars(
            Market.SH,
            "600519",
            "1m",
            "today",
            "none",
            datetime(2026, 8, 4, 10, 31, tzinfo=TZ),
        )
    )
    assert await asyncio.to_thread(read_started.wait, 2)

    task.cancel()
    await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.inflight_count == 1

    release_read.set()
    while service.inflight_count:
        await asyncio.sleep(0)
    assert service.inflight_count == 0


async def test_daily_history_write_runs_outside_event_loop(
    monkeypatch, repository, fake_source
):
    repository.replace_trading_days(
        set(_trading_days_ending(date(2026, 8, 4), 80))
    )
    event_loop_thread = get_ident()
    write_threads: list[int] = []
    original_upsert_bars = repository.upsert_bars

    def recording_upsert_bars(*args, **kwargs):
        write_threads.append(get_ident())
        return original_upsert_bars(*args, **kwargs)

    monkeypatch.setattr(repository, "upsert_bars", recording_upsert_bars)
    service = BarService(fake_source, repository, history_days=60)

    await service.ensure_daily_history(
        Stock("600519", Market.SH, "贵州茅台"), date(2026, 8, 4)
    )

    assert len(write_threads) == 1
    assert write_threads[0] != event_loop_thread


async def test_history_bootstrap_stock_list_runs_outside_event_loop(
    monkeypatch, repository, fake_source
):
    repository.upsert_stocks(fake_source.stock_rows)
    event_loop_thread = get_ident()
    read_threads: list[int] = []
    original_list_all_stocks = repository.list_all_stocks

    def recording_list_all_stocks():
        read_threads.append(get_ident())
        return original_list_all_stocks()

    async def do_nothing(stock, end_date):
        return []

    monkeypatch.setattr(repository, "list_all_stocks", recording_list_all_stocks)
    service = BarService(fake_source, repository, history_days=60)
    monkeypatch.setattr(service, "ensure_daily_history", do_nothing)

    await HistoryBootstrapper(service, repository, delay_seconds=0).run()

    assert len(read_threads) == 1
    assert read_threads[0] != event_loop_thread


def test_query_validation_uses_dedicated_exception(repository, fake_source):
    from a_share_radar.services.bar_service import BarQueryValidationError

    service = BarService(fake_source, repository, history_days=60)

    with pytest.raises(BarQueryValidationError, match="今日视图"):
        service._validate("1d", "today", "none")


async def test_minute_source_failure_returns_saved_bars(repository, fake_source):
    repository.upsert_bars([fake_source.bar_rows[0]])
    fake_source.minute_error = RuntimeError("模拟分钟接口不可用")
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)

    bars = await service.get_bars(
        Market.SH, "600519", "1m", "today", "none", now
    )

    assert bars == [fake_source.bar_rows[0]]


async def test_minute_source_failure_without_cache_raises(repository, fake_source):
    fake_source.minute_error = RuntimeError("模拟分钟接口不可用")
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)

    with pytest.raises(RuntimeError, match="模拟分钟接口不可用"):
        await service.get_bars(
            Market.SH, "600519", "1m", "today", "none", now
        )


async def test_same_key_concurrent_requests_access_source_serially(
    monkeypatch, repository, fake_source
):
    active = 0
    maximum_active = 0
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def controlled_fetch(code, start, end, period, adjustment):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        if not first_started.is_set():
            first_started.set()
            await release_first.wait()
        await asyncio.sleep(0)
        active -= 1
        return [fake_source.bar_rows[0]]

    monkeypatch.setattr(fake_source, "fetch_minute_bars", controlled_fetch)
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)
    first = asyncio.create_task(
        service.get_bars(Market.SH, "600519", "1m", "today", "none", now)
    )
    await first_started.wait()
    second = asyncio.create_task(
        service.get_bars(Market.SH, "600519", "1m", "today", "none", now)
    )
    await asyncio.sleep(0)

    assert maximum_active == 1
    assert service.inflight_count == 1
    release_first.set()
    await asyncio.gather(first, second)
    assert maximum_active == 1
    assert service.inflight_count == 0


async def test_keyed_locks_are_reclaimed_after_multiple_keys(repository, fake_source):
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)

    await service.get_bars(Market.SH, "600519", "1m", "today", "none", now)
    await service.get_bars(Market.SZ, "000001", "1m", "today", "none", now)

    assert service.inflight_count == 0


async def test_keyed_lock_is_reclaimed_after_source_error(repository, fake_source):
    fake_source.minute_error = RuntimeError("模拟分钟接口不可用")
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)

    with pytest.raises(RuntimeError, match="模拟分钟接口不可用"):
        await service.get_bars(
            Market.SH, "600519", "1m", "today", "none", now
        )

    await asyncio.sleep(0)
    assert service.inflight_count == 0


async def test_waiter_cancellation_keeps_lock_until_holder_exits(
    monkeypatch, repository, fake_source
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def controlled_fetch(code, start, end, period, adjustment):
        started.set()
        await release.wait()
        return [fake_source.bar_rows[0]]

    monkeypatch.setattr(fake_source, "fetch_minute_bars", controlled_fetch)
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)
    holder = asyncio.create_task(
        service.get_bars(Market.SH, "600519", "1m", "today", "none", now)
    )
    await started.wait()
    waiter = asyncio.create_task(
        service.get_bars(Market.SH, "600519", "1m", "today", "none", now)
    )
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert service.inflight_count == 1

    release.set()
    await holder
    assert service.inflight_count == 0


async def test_only_waiter_cancellation_leaves_shared_fetch_for_service_shutdown(
    monkeypatch, repository, fake_source
):
    started = asyncio.Event()

    async def wait_forever(code, start, end, period, adjustment):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(fake_source, "fetch_minute_bars", wait_forever)
    service = BarService(fake_source, repository, history_days=60)
    now = datetime(2026, 8, 4, 10, 31, tzinfo=TZ)
    holder = asyncio.create_task(
        service.get_bars(Market.SH, "600519", "1m", "today", "none", now)
    )
    await started.wait()

    holder.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder

    assert service.inflight_count == 1
    await service.close()
    assert service.inflight_count == 0


async def test_history_bootstrapper_continues_after_one_stock_fails(
    monkeypatch, repository, fake_source
):
    repository.upsert_stocks(fake_source.stock_rows)
    service = BarService(fake_source, repository, history_days=60)
    attempted: list[str] = []

    async def ensure(stock, end_date):
        attempted.append(stock.code)
        if stock.code == "600519":
            raise RuntimeError("模拟单股失败")
        return []

    monkeypatch.setattr(service, "ensure_daily_history", ensure)

    await HistoryBootstrapper(service, repository, delay_seconds=0).run()

    assert attempted == ["600519", "000001"]


async def test_history_bootstrapper_exits_when_cancelled(repository, fake_source):
    repository.upsert_stocks(fake_source.stock_rows)
    service = BarService(fake_source, repository, history_days=60)
    started = asyncio.Event()

    async def wait_forever(stock, end_date):
        started.set()
        await asyncio.Event().wait()

    service.ensure_daily_history = wait_forever
    task = asyncio.create_task(
        HistoryBootstrapper(service, repository, delay_seconds=0).run()
    )
    await started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_create_app_exposes_injected_repository_and_bar_service_without_lifespan(
    repository, fake_source
):
    app = create_app(source=fake_source, database=repository.database)

    assert app.state.repository.database is repository.database
    assert app.state.bar_service.repository is app.state.repository
    assert app.state.bar_service.source is fake_source
