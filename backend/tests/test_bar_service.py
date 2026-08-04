import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from a_share_radar.domain.models import Market, Stock
from a_share_radar.main import create_app
from a_share_radar.services.bar_service import BarService, HistoryBootstrapper

TZ = ZoneInfo("Asia/Shanghai")


async def test_daily_history_requests_sixty_trading_days(repository, fake_source):
    service = BarService(fake_source, repository, history_days=60)

    await service.ensure_daily_history(
        Stock("600519", Market.SH, "贵州茅台"), date(2026, 8, 4)
    )

    request = fake_source.daily_requests[0]
    assert request.code == "600519"
    assert request.end == date(2026, 8, 4)
    assert (request.end - request.start).days >= 84


async def test_daily_history_only_saves_latest_sixty_bars(repository, fake_source):
    template = fake_source.bar_rows[1]
    fake_source.bar_rows = [
        replace(template, bar_time=template.bar_time - timedelta(days=offset))
        for offset in reversed(range(65))
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
    release_first.set()
    await asyncio.gather(first, second)
    assert maximum_active == 1


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
