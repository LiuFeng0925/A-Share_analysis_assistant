import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

import a_share_radar.data_sources.akshare_source as akshare_module
from a_share_radar.data_sources.akshare_source import AkshareSource
from a_share_radar.domain.models import Market, QualityStatus, Stock
from a_share_radar.main import create_app
from a_share_radar.services.bar_service import BarService

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def _seed_known_stocks(repository, fake_source):
    repository.upsert_stocks(fake_source.stock_rows)


def _trading_days(end: date, count: int) -> list[date]:
    result: list[date] = []
    cursor = end
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(result))


async def test_initial_history_uses_exact_sixty_day_calendar_start(
    repository, fake_source
):
    days = _trading_days(date(2026, 8, 4), 80)
    repository.replace_trading_days(set(days))
    service = BarService(fake_source, repository, history_days=60)

    await service.ensure_daily_history(
        Stock("600519", Market.SH, "贵州茅台"), date(2026, 8, 4)
    )

    assert fake_source.daily_requests[0].start == days[-60]


async def test_initial_history_refuses_incomplete_calendar_without_partial_write(
    repository, fake_source
):
    repository.replace_trading_days(
        set(_trading_days(date(2026, 8, 4), 41))
    )
    service = BarService(fake_source, repository, history_days=60)

    with pytest.raises(RuntimeError, match="交易日历.*60"):
        await service.ensure_daily_history(
            Stock("600519", Market.SH, "贵州茅台"), date(2026, 8, 4)
        )

    assert fake_source.daily_requests == []
    assert repository.data_status().bar_count == 0


async def test_daily_history_retries_internal_calendar_gap_even_with_latest_bar(
    repository, fake_source
):
    days = _trading_days(date(2026, 8, 4), 80)
    repository.replace_trading_days(set(days))
    template = fake_source.bar_rows[1]
    missing_day = days[-30]
    existing = [
        replace(
            template,
            bar_time=datetime(day.year, day.month, day.day, 15, 0, tzinfo=TZ),
            acquired_at=datetime(day.year, day.month, day.day, 15, 10, tzinfo=TZ),
        )
        for day in days[-60:]
        if day != missing_day
    ]
    repository.upsert_bars(existing)
    fake_source.bar_rows = [
        replace(
            template,
            bar_time=datetime(
                missing_day.year, missing_day.month, missing_day.day, 15, 0, tzinfo=TZ
            ),
            acquired_at=datetime(2026, 8, 4, 15, 20, tzinfo=TZ),
        )
    ]
    service = BarService(fake_source, repository, history_days=60)

    bars = await service.ensure_daily_history(
        Stock("600519", Market.SH, "贵州茅台"), date(2026, 8, 4)
    )

    assert len(fake_source.daily_requests) == 1
    assert fake_source.daily_requests[0].start == missing_day
    assert len(bars) == 60


async def test_completion_uses_provider_end_label_and_bar_acquisition_time(
    repository, fake_source
):
    bar = replace(
        fake_source.bar_rows[0],
        bar_time=datetime(2026, 8, 4, 10, 31, tzinfo=TZ),
        acquired_at=datetime(2026, 8, 4, 10, 31, 59, tzinfo=TZ),
        is_complete=True,
        quality_status=QualityStatus.OK,
    )
    fake_source.bar_rows = [bar]
    service = BarService(fake_source, repository, history_days=60)

    bars = await service.get_bars(
        Market.SH,
        "600519",
        "1m",
        "today",
        "none",
        datetime(2026, 8, 4, 10, 32, 5, tzinfo=TZ),
    )

    assert bars[-1].is_complete is True
    assert bars[-1].quality_status is QualityStatus.OK


async def test_background_and_detail_share_same_daily_provider_request(
    repository, fake_source, monkeypatch
):
    calendar = _trading_days(date(2026, 8, 4), 80)
    repository.replace_trading_days(set(calendar))
    cached = replace(
        fake_source.bar_rows[1],
        bar_time=datetime(2026, 8, 3, 15, 0, tzinfo=TZ),
        acquired_at=datetime(2026, 8, 3, 15, 10, tzinfo=TZ),
    )
    repository.upsert_bars([cached])
    today = replace(
        cached,
        bar_time=datetime(2026, 8, 4, 15, 0, tzinfo=TZ),
        acquired_at=datetime(2026, 8, 4, 15, 20, tzinfo=TZ),
    )
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def controlled_fetch(code, start, end, period, adjustment):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return [today]

    monkeypatch.setattr(fake_source, "fetch_daily_bars", controlled_fetch)
    service = BarService(fake_source, repository, history_days=60)
    background = asyncio.create_task(
        service.ensure_daily_history(
            Stock("600519", Market.SH, "贵州茅台"), date(2026, 8, 4)
        )
    )
    await started.wait()
    detail = asyncio.create_task(
        service.get_bars(
            Market.SH,
            "600519",
            "1d",
            "5d",
            "qfq",
            datetime(2026, 8, 4, 15, 20, tzinfo=TZ),
        )
    )
    await asyncio.sleep(0)

    calls_while_shared = calls
    release.set()
    await asyncio.gather(background, detail)
    assert calls_while_shared == 1
    assert calls == 1


async def test_close_waits_until_real_akshare_provider_thread_finishes(
    repository, monkeypatch
):
    thread_started = Event()
    release_thread = Event()

    def blocking_provider(**kwargs):
        thread_started.set()
        release_thread.wait()
        return pd.DataFrame(
            [
                {
                    "时间": "2026-08-04 10:31:00",
                    "开盘": 10.1,
                    "收盘": 10.2,
                    "最高": 10.3,
                    "最低": 10.0,
                    "成交量": 1_000,
                    "成交额": 1_020_000.0,
                }
            ]
        )

    monkeypatch.setattr(
        akshare_module.ak, "stock_zh_a_hist_min_em", blocking_provider
    )
    service = BarService(AkshareSource(), repository, history_days=60)
    query = asyncio.create_task(
        service.get_bars(
            Market.SZ,
            "000001",
            "1m",
            "today",
            "none",
            datetime(2026, 8, 4, 10, 32, tzinfo=TZ),
        )
    )
    assert await asyncio.to_thread(thread_started.wait, 2)

    closing = asyncio.create_task(service.close())
    await asyncio.sleep(0.05)
    closed_before_release = closing.done()
    release_thread.set()
    await closing
    assert closed_before_release is False
    with pytest.raises(asyncio.CancelledError):
        await query


async def test_recent_query_cache_is_globally_pruned_and_bounded(
    repository, fake_source
):
    repository.upsert_stocks(
        [Stock(f"{index:06d}", Market.SZ, f"测试股票{index}") for index in range(12)]
    )
    service = BarService(
        fake_source,
        repository,
        history_days=60,
        query_ttl_seconds=60,
        query_cache_max_entries=3,
    )
    now = datetime(2026, 8, 4, 10, 32, tzinfo=TZ)

    for index in range(12):
        await service.get_bars(
            Market.SZ,
            f"{index:06d}",
            "1m",
            "today",
            "none",
            now,
        )

    assert service.recent_cache_size == 3


async def test_all_bad_bars_leave_partial_ingestion_audit(
    repository, monkeypatch
):
    frame = pd.DataFrame(
        [
            {
                "时间": "2026-08-04 10:31:00",
                "开盘": 0,
                "收盘": 10.2,
                "最高": 10.3,
                "最低": 10.0,
                "成交量": 1_000,
                "成交额": 1_020_000.0,
            }
        ]
    )
    monkeypatch.setattr(
        akshare_module.ak, "stock_zh_a_hist_min_em", lambda **kwargs: frame
    )
    service = BarService(AkshareSource(), repository, history_days=60)

    bars = await service.get_bars(
        Market.SZ,
        "000001",
        "1m",
        "today",
        "none",
        datetime(2026, 8, 4, 10, 32, tzinfo=TZ),
    )
    audit = repository.latest_bar_ingestion(
        Market.SZ, "000001", "1m", "none"
    )

    assert bars == []
    assert audit is not None
    assert audit.quality_status == "partial"
    assert audit.raw_row_count == 1
    assert audit.valid_row_count == 0
    assert audit.invalid_row_count == 1


async def test_all_bad_bar_api_reports_partial_instead_of_never_fetched(
    repository, monkeypatch
):
    frame = pd.DataFrame(
        [
            {
                "时间": "2026-08-04 10:31:00",
                "开盘": 0,
                "收盘": 10.2,
                "最高": 10.3,
                "最低": 10.0,
                "成交量": 1_000,
                "成交额": 1_020_000.0,
            }
        ]
    )
    monkeypatch.setattr(
        akshare_module.ak, "stock_zh_a_hist_min_em", lambda **kwargs: frame
    )
    app = create_app(
        source=AkshareSource(),
        database=repository.database,
        now_provider=lambda: datetime(2026, 8, 4, 10, 32, tzinfo=TZ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/stocks/SZ/000001/bars",
            params={"period": "1m", "range": "today", "adjustment": "none"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["last_updated_at"] is None
    assert body["fetch_quality_status"] == "partial"
    assert body["fetch_raw_row_count"] == 1
    assert body["fetch_valid_row_count"] == 0
    assert body["fetch_invalid_row_count"] == 1
    assert body["last_fetch_at"].endswith("+08:00")


async def test_non_numeric_bad_bar_is_counted_as_partial_instead_of_error(
    repository, monkeypatch
):
    frame = pd.DataFrame(
        [
            {
                "时间": "2026-08-04 10:31:00",
                "开盘": "-",
                "收盘": 10.2,
                "最高": 10.3,
                "最低": 10.0,
                "成交量": "-",
                "成交额": None,
            }
        ]
    )
    monkeypatch.setattr(
        akshare_module.ak, "stock_zh_a_hist_min_em", lambda **kwargs: frame
    )
    service = BarService(AkshareSource(), repository, history_days=60)

    bars = await service.get_bars(
        Market.SZ,
        "000001",
        "1m",
        "today",
        "none",
        datetime(2026, 8, 4, 10, 32, tzinfo=TZ),
    )
    audit = repository.latest_bar_ingestion(
        Market.SZ, "000001", "1m", "none"
    )

    assert bars == []
    assert audit is not None
    assert audit.status == "success"
    assert audit.quality_status == "partial"
    assert audit.raw_row_count == 1
    assert audit.invalid_row_count == 1


async def test_akshare_records_acquired_at_after_normalization(monkeypatch):
    events: list[str] = []

    def provider(**kwargs):
        events.append("network")
        return pd.DataFrame()

    def normalize(*args, **kwargs):
        events.append("normalize")
        return []

    class RecordingDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            events.append("acquired_at")
            return datetime(2026, 8, 4, 10, 32, tzinfo=tz)

    monkeypatch.setattr(
        akshare_module.ak, "stock_zh_a_hist_min_em", provider
    )
    monkeypatch.setattr(AkshareSource, "normalize_minute_bars", normalize)
    monkeypatch.setattr(akshare_module, "datetime", RecordingDateTime)

    await AkshareSource().fetch_minute_bars(
        "000001",
        datetime(2026, 8, 4, 9, 30, tzinfo=TZ),
        datetime(2026, 8, 4, 10, 32, tzinfo=TZ),
        "1m",
        "none",
    )

    assert events == ["network", "normalize", "acquired_at"]


def test_implementation_plan_uses_share_volume_examples():
    plan = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-08-04-a股行情系统-mvp-实施计划.md"
    ).read_text(encoding="utf-8")

    assert "assert quotes[0].volume == 33455\n" not in plan
    assert "assert quotes[0].volume == 3345500" in plan
