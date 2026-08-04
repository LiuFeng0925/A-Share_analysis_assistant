import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pytest

import a_share_radar.data_sources.akshare_source as akshare_module
from a_share_radar.data_sources.akshare_source import AkshareSource
from a_share_radar.domain.models import (
    BarFetchBatch,
    Market,
    QualityStatus,
    QuoteSnapshot,
    Stock,
)
from a_share_radar.services.bar_service import BarService
from a_share_radar.storage.database import Database

TZ = ZoneInfo("Asia/Shanghai")


async def test_wide_range_then_narrow_range_uses_one_serial_provider_call(
    repository, fake_source, monkeypatch
):
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[tuple[date, date]] = []
    active = 0
    maximum_active = 0

    async def controlled_fetch(code, start, end, period, adjustment):
        nonlocal active, maximum_active
        calls.append((start, end))
        active += 1
        maximum_active = max(maximum_active, active)
        if len(calls) == 1:
            started.set()
            await release.wait()
        active -= 1
        return []

    monkeypatch.setattr(fake_source, "fetch_daily_bars", controlled_fetch)
    service = BarService(fake_source, repository, history_days=60)
    wide = asyncio.create_task(
        service._fetch_daily_provider(
            Market.SH,
            "600519",
            date(2026, 6, 1),
            date(2026, 8, 31),
            "1d",
            "qfq",
        )
    )
    await started.wait()
    narrow = asyncio.create_task(
        service._fetch_daily_provider(
            Market.SH,
            "600519",
            date(2026, 7, 1),
            date(2026, 7, 31),
            "1d",
            "qfq",
        )
    )
    await asyncio.sleep(0.05)

    assert maximum_active == 1
    assert len(calls) == 1
    release.set()
    await asyncio.gather(wide, narrow)
    assert calls == [(date(2026, 6, 1), date(2026, 8, 31))]


async def test_narrow_range_then_wide_range_serializes_and_only_fetches_remainder(
    repository, fake_source, monkeypatch
):
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[tuple[date, date]] = []
    active = 0
    maximum_active = 0

    async def controlled_fetch(code, start, end, period, adjustment):
        nonlocal active, maximum_active
        calls.append((start, end))
        active += 1
        maximum_active = max(maximum_active, active)
        if len(calls) == 1:
            started.set()
            await release.wait()
        active -= 1
        return []

    monkeypatch.setattr(fake_source, "fetch_daily_bars", controlled_fetch)
    service = BarService(fake_source, repository, history_days=60)
    narrow = asyncio.create_task(
        service._fetch_daily_provider(
            Market.SH,
            "600519",
            date(2026, 7, 1),
            date(2026, 7, 31),
            "1d",
            "qfq",
        )
    )
    await started.wait()
    wide = asyncio.create_task(
        service._fetch_daily_provider(
            Market.SH,
            "600519",
            date(2026, 6, 1),
            date(2026, 8, 31),
            "1d",
            "qfq",
        )
    )
    await asyncio.sleep(0.05)

    assert maximum_active == 1
    assert len(calls) == 1
    release.set()
    await asyncio.gather(narrow, wide)

    assert maximum_active == 1
    assert calls == [
        (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 8, 1), date(2026, 8, 31)),
    ]


async def test_cancelled_range_waiter_does_not_cancel_series_coordination(
    repository, fake_source, monkeypatch
):
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[tuple[date, date]] = []
    active = 0
    maximum_active = 0

    async def controlled_fetch(code, start, end, period, adjustment):
        nonlocal active, maximum_active
        calls.append((start, end))
        active += 1
        maximum_active = max(maximum_active, active)
        if len(calls) == 1:
            started.set()
            await release.wait()
        active -= 1
        return []

    monkeypatch.setattr(fake_source, "fetch_daily_bars", controlled_fetch)
    service = BarService(fake_source, repository, history_days=60)
    cancelled = asyncio.create_task(
        service._fetch_daily_provider(
            Market.SH,
            "600519",
            date(2026, 7, 1),
            date(2026, 7, 31),
            "1d",
            "qfq",
        )
    )
    await started.wait()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    wide = asyncio.create_task(
        service._fetch_daily_provider(
            Market.SH,
            "600519",
            date(2026, 6, 1),
            date(2026, 8, 31),
            "1d",
            "qfq",
        )
    )
    release.set()
    await wide

    assert maximum_active == 1
    assert calls == [
        (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 8, 1), date(2026, 8, 31)),
    ]


def test_minute_range_remainder_keeps_seconds_after_confirmed_range():
    request_start = datetime(2026, 8, 4, 9, 30, tzinfo=TZ)
    request_end = datetime(2026, 8, 4, 10, 32, 5, tzinfo=TZ)
    confirmed_end = datetime(2026, 8, 4, 10, 31, 59, tzinfo=TZ)

    remaining = BarService._subtract_confirmed_ranges(
        request_start,
        request_end,
        [(request_start, confirmed_end)],
        "1m",
    )

    assert remaining == [
        (
            datetime(2026, 8, 4, 10, 32, tzinfo=TZ),
            request_end,
        )
    ]


async def test_successful_empty_range_survives_restart_but_expires_for_later_data(
    repository, fake_source, monkeypatch
):
    calls = 0
    returned_bars: list = []

    async def controlled_fetch(code, start, end, period, adjustment):
        nonlocal calls
        calls += 1
        return BarFetchBatch(
            tuple(returned_bars),
            datetime.now(TZ),
            "fixture",
            QualityStatus.OK,
            len(returned_bars),
            0,
        )

    monkeypatch.setattr(fake_source, "fetch_daily_bars", controlled_fetch)
    request = (
        Market.SH,
        "600519",
        date(2026, 7, 1),
        date(2026, 7, 31),
        "1d",
        "qfq",
    )
    first = BarService(fake_source, repository, history_days=60)
    assert await first._fetch_daily_provider(*request) == []
    await first.close()

    second = BarService(fake_source, repository, history_days=60)
    assert await second._fetch_daily_provider(*request) == []
    assert calls == 1
    await second.close()

    with repository.database.lock:
        empty_status = repository.database.connection.execute(
            "SELECT status FROM bar_range_check"
        ).fetchone()[0]
    assert empty_status == "success_empty"

    with repository.database.lock:
        repository.database.connection.execute(
            "UPDATE bar_range_check SET checked_at = ?",
            (datetime.now(TZ) - timedelta(days=8),),
        )
    returned_bars.append(
        replace(
            fake_source.bar_rows[1],
            bar_time=datetime(2026, 7, 15, 15, 0, tzinfo=TZ),
            acquired_at=datetime.now(TZ),
        )
    )
    third = BarService(fake_source, repository, history_days=60)
    bars = await third._fetch_daily_provider(*request)

    assert calls == 2
    assert [bar.bar_time.date() for bar in bars] == [date(2026, 7, 15)]


async def test_partial_range_is_persisted_idempotently_and_retried_after_restart(
    repository, fake_source, monkeypatch
):
    calls = 0

    async def partial_fetch(code, start, end, period, adjustment):
        nonlocal calls
        calls += 1
        return BarFetchBatch(
            (),
            datetime.now(TZ),
            "fixture",
            QualityStatus.PARTIAL,
            1,
            1,
        )

    monkeypatch.setattr(fake_source, "fetch_daily_bars", partial_fetch)
    request = (
        Market.SH,
        "600519",
        date(2026, 7, 1),
        date(2026, 7, 31),
        "1d",
        "qfq",
    )
    await BarService(fake_source, repository, history_days=60)._fetch_daily_provider(
        *request
    )
    await BarService(fake_source, repository, history_days=60)._fetch_daily_provider(
        *request
    )

    with repository.database.lock:
        rows = repository.database.connection.execute(
            "SELECT status, quality_status, raw_row_count, invalid_row_count "
            "FROM bar_range_check"
        ).fetchall()
    assert calls == 2
    assert rows == [("partial", "partial", 1, 1)]


async def test_failed_range_is_persisted_and_retried_after_restart(
    repository, fake_source, monkeypatch
):
    calls = 0

    async def failing_fetch(code, start, end, period, adjustment):
        nonlocal calls
        calls += 1
        raise RuntimeError("模拟范围抓取失败")

    monkeypatch.setattr(fake_source, "fetch_daily_bars", failing_fetch)
    request = (
        Market.SH,
        "600519",
        date(2026, 7, 1),
        date(2026, 7, 31),
        "1d",
        "qfq",
    )
    with pytest.raises(RuntimeError, match="模拟范围抓取失败"):
        await BarService(
            fake_source, repository, history_days=60
        )._fetch_daily_provider(*request)
    with pytest.raises(RuntimeError, match="模拟范围抓取失败"):
        await BarService(
            fake_source, repository, history_days=60
        )._fetch_daily_provider(*request)

    with repository.database.lock:
        rows = repository.database.connection.execute(
            "SELECT status, quality_status FROM bar_range_check"
        ).fetchall()
    assert calls == 2
    assert rows == [("failed", "error")]


async def test_daily_history_clips_target_days_before_stock_listing(
    repository, fake_source
):
    trading_days: list[date] = []
    cursor = date(2026, 8, 4)
    while len(trading_days) < 80:
        if cursor.weekday() < 5:
            trading_days.append(cursor)
        cursor -= timedelta(days=1)
    trading_days.reverse()
    repository.replace_trading_days(set(trading_days))
    list_date = trading_days[-10]
    service = BarService(fake_source, repository, history_days=60)

    await service.ensure_daily_history(
        Stock("600519", Market.SH, "贵州茅台", list_date=list_date),
        date(2026, 8, 4),
    )

    assert fake_source.daily_requests[0].start == list_date
    assert fake_source.daily_requests[0].end == date(2026, 8, 4)


def test_stock_master_refresh_preserves_existing_listing_date(repository):
    listing_date = date(2001, 8, 27)
    repository.upsert_stocks(
        [Stock("600519", Market.SH, "贵州茅台", list_date=listing_date)]
    )

    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台股份", list_date=None)])

    saved = repository.list_all_stocks()[0]
    assert saved.name == "贵州茅台股份"
    assert saved.list_date == listing_date


async def test_akshare_stock_master_merges_exchange_listing_dates(
    fake_source, monkeypatch
):
    base = fake_source.snapshot_rows[0]
    snapshots = [
        replace(base, code="600519", market=Market.SH, name="贵州茅台"),
        replace(base, code="688001", market=Market.SH, name="华兴源创"),
        replace(base, code="000001", market=Market.SZ, name="平安银行"),
        replace(base, code="920092", market=Market.BJ, name="汉鑫科技"),
    ]
    source = AkshareSource()

    async def snapshots_from_fixture():
        return snapshots

    monkeypatch.setattr(source, "fetch_market_snapshot", snapshots_from_fixture)
    monkeypatch.setattr(
        akshare_module.ak,
        "stock_info_sh_name_code",
        lambda symbol: pd.DataFrame(
            [
                {
                    "证券代码": "600519" if symbol == "主板A股" else "688001",
                    "上市日期": (
                        date(2001, 8, 27)
                        if symbol == "主板A股"
                        else date(2019, 7, 22)
                    ),
                }
            ]
        ),
    )
    monkeypatch.setattr(
        akshare_module.ak,
        "stock_info_sz_name_code",
        lambda symbol: pd.DataFrame(
            [{"A股代码": "000001", "A股上市日期": date(1991, 4, 3)}]
        ),
    )
    monkeypatch.setattr(
        akshare_module.ak,
        "stock_info_bj_name_code",
        lambda: pd.DataFrame(
            [{"证券代码": "920092", "上市日期": date(2021, 11, 15)}]
        ),
    )

    stocks = await source.fetch_stock_master()

    assert {stock.code: stock.list_date for stock in stocks} == {
        "600519": date(2001, 8, 27),
        "688001": date(2019, 7, 22),
        "000001": date(1991, 4, 3),
        "920092": date(2021, 11, 15),
    }


async def test_provider_lock_pool_stays_bounded_for_many_series(
    repository, fake_source
):
    service = BarService(fake_source, repository, history_days=60)

    for index in range(270):
        await service._fetch_daily_provider(
            Market.SZ,
            f"{index:06d}",
            date(2026, 8, 4),
            date(2026, 8, 4),
            "1d",
            "qfq",
        )

    assert len(service._provider_locks) <= 256


def test_legacy_database_creates_bar_range_check_migration(tmp_path: Path):
    path = tmp_path / "legacy-range.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE legacy_marker (value INTEGER)")
    connection.close()

    database = Database(path)
    try:
        columns = {
            row[1]
            for row in database.connection.execute(
                "PRAGMA table_info('bar_range_check')"
            ).fetchall()
        }
    finally:
        database.close()

    assert {
        "market",
        "code",
        "period",
        "adjustment",
        "range_start",
        "range_end",
        "checked_at",
        "status",
        "quality_status",
    } <= columns


async def test_snapshot_captured_at_is_assigned_after_normalization(monkeypatch):
    events: list[str] = []
    provisional_times: list[datetime] = []

    def provider():
        events.append("network")
        return pd.DataFrame([{"代码": "600519"}])

    def normalize(cls, frame, captured_at):
        events.append("normalize")
        provisional_times.append(captured_at)
        return [
            QuoteSnapshot(
                code="600519",
                market=Market.SH,
                name="贵州茅台",
                captured_at=captured_at,
                latest_price=10.0,
                change_percent=0.0,
                change_amount=0.0,
                open_price=10.0,
                high_price=10.0,
                low_price=10.0,
                previous_close=10.0,
                volume=100,
                amount=1_000.0,
                turnover_rate=0.1,
                total_market_cap=1_000_000.0,
                source="fixture",
                quality_status=QualityStatus.OK,
            )
        ]

    class RecordingDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            events.append("captured_at")
            return datetime(2026, 8, 4, 10, 32, tzinfo=tz)

    monkeypatch.setattr(akshare_module.ak, "stock_zh_a_spot_em", provider)
    monkeypatch.setattr(AkshareSource, "normalize_snapshot", classmethod(normalize))
    monkeypatch.setattr(akshare_module, "datetime", RecordingDateTime)

    quotes = await AkshareSource().fetch_market_snapshot()

    assert events == ["network", "normalize", "captured_at"]
    assert quotes[0].captured_at == datetime(2026, 8, 4, 10, 32, tzinfo=TZ)
    assert provisional_times[0] != quotes[0].captured_at


def test_readme_describes_target_set_gap_recovery():
    readme = (
        Path(__file__).resolve().parents[2] / "README.md"
    ).read_text(encoding="utf-8")

    assert "重启只从本地最后一根完成柱后续传" not in readme
    assert "目标交易日集合" in readme
    assert "内部缺口" in readme
