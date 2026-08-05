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
        empty_status, checked_at_text, expires_at_text = (
            repository.database.connection.execute(
                "SELECT status, CAST(checked_at AS VARCHAR), "
                "CAST(expires_at AS VARCHAR) FROM bar_range_check"
            ).fetchone()
        )
    assert empty_status == "success_empty"
    checked_at = datetime.fromisoformat(checked_at_text)
    expires_at = datetime.fromisoformat(expires_at_text)
    assert expires_at - checked_at >= timedelta(days=6)

    with repository.database.lock:
        repository.database.connection.execute(
            "UPDATE bar_range_check SET expires_at = ?",
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


async def test_morning_empty_daily_range_is_refetched_at_daily_close(
    repository, fake_source, monkeypatch
):
    trading_days: list[date] = []
    cursor = date(2026, 8, 4)
    while len(trading_days) < 80:
        if cursor.weekday() < 5:
            trading_days.append(cursor)
        cursor -= timedelta(days=1)
    trading_days.reverse()
    repository.replace_trading_days(set(trading_days))
    clock = [datetime(2026, 8, 4, 10, 0, tzinfo=TZ)]
    calls = 0
    formal_bar = replace(
        fake_source.bar_rows[1],
        bar_time=datetime(2026, 8, 4, 15, 0, tzinfo=TZ),
        acquired_at=datetime(2026, 8, 4, 15, 20, tzinfo=TZ),
    )

    async def controlled_fetch(code, start, end, period, adjustment):
        nonlocal calls
        calls += 1
        bars = () if calls == 1 else (formal_bar,)
        return BarFetchBatch(
            bars,
            clock[0],
            "fixture",
            QualityStatus.OK,
            len(bars),
            0,
        )

    monkeypatch.setattr(fake_source, "fetch_daily_bars", controlled_fetch)
    service = BarService(
        fake_source,
        repository,
        history_days=60,
        now_provider=lambda: clock[0],
    )
    stock = Stock("600519", Market.SH, "贵州茅台")

    await service.ensure_daily_history(stock, date(2026, 8, 4))
    await asyncio.sleep(0)
    clock[0] = datetime(2026, 8, 4, 15, 20, tzinfo=TZ)
    bars = await service.ensure_daily_history(stock, date(2026, 8, 4))

    assert calls == 2
    assert bars[-1].bar_time == formal_bar.bar_time


async def test_empty_current_minute_range_refetches_late_bar_on_next_overlap(
    repository, fake_source, monkeypatch
):
    clock = [datetime(2026, 8, 4, 10, 31, 30, tzinfo=TZ)]
    calls = 0
    late_bar = replace(
        fake_source.bar_rows[0],
        code="600519",
        market=Market.SH,
        period="1m",
        adjustment="none",
        bar_time=datetime(2026, 8, 4, 10, 31, tzinfo=TZ),
        acquired_at=datetime(2026, 8, 4, 10, 32, 5, tzinfo=TZ),
    )

    async def controlled_fetch(code, start, end, period, adjustment):
        nonlocal calls
        calls += 1
        bars = () if calls == 1 else (late_bar,)
        return BarFetchBatch(
            bars,
            clock[0],
            "fixture",
            QualityStatus.OK,
            len(bars),
            0,
        )

    monkeypatch.setattr(fake_source, "fetch_minute_bars", controlled_fetch)
    service = BarService(
        fake_source,
        repository,
        history_days=60,
        now_provider=lambda: clock[0],
    )

    await service._fetch_minute_provider(
        Market.SH,
        "600519",
        datetime(2026, 8, 4, 9, 30, tzinfo=TZ),
        clock[0],
        "1m",
        "none",
    )
    clock[0] = datetime(2026, 8, 4, 10, 32, 5, tzinfo=TZ)
    await service._fetch_minute_provider(
        Market.SH,
        "600519",
        datetime(2026, 8, 4, 10, 30, tzinfo=TZ),
        clock[0],
        "1m",
        "none",
    )
    saved = repository.get_bars(
        Market.SH,
        "600519",
        "1m",
        datetime(2026, 8, 4, 9, 30, tzinfo=TZ),
        clock[0],
        "none",
    )

    assert calls == 2
    assert [bar.bar_time for bar in saved] == [late_bar.bar_time]


@pytest.mark.parametrize("period", ["1d", "1w", "1mo"])
async def test_current_history_range_uses_short_expiry_even_when_empty(
    period, repository, fake_source, monkeypatch
):
    acquired_at = datetime(2026, 8, 4, 10, 0, tzinfo=TZ)

    async def empty_fetch(code, start, end, requested_period, adjustment):
        return BarFetchBatch(
            (), acquired_at, "fixture", QualityStatus.OK, 0, 0
        )

    monkeypatch.setattr(fake_source, "fetch_daily_bars", empty_fetch)
    service = BarService(
        fake_source,
        repository,
        history_days=60,
        now_provider=lambda: acquired_at,
    )

    await service._fetch_daily_provider(
        Market.SH,
        "600519",
        date(2026, 7, 1),
        acquired_at.date(),
        period,
        "qfq",
    )

    with repository.database.lock:
        checked_text, expires_text = repository.database.connection.execute(
            "SELECT CAST(checked_at AS VARCHAR), CAST(expires_at AS VARCHAR) "
            "FROM bar_range_check"
        ).fetchone()
    assert datetime.fromisoformat(expires_text) - datetime.fromisoformat(
        checked_text
    ) <= timedelta(seconds=5)


async def test_dynamic_minute_range_with_data_still_uses_short_expiry(
    repository, fake_source, monkeypatch
):
    acquired_at = datetime(2026, 8, 4, 10, 31, 30, tzinfo=TZ)
    complete_bar = replace(
        fake_source.bar_rows[0],
        code="600519",
        market=Market.SH,
        period="1m",
        adjustment="none",
        bar_time=datetime(2026, 8, 4, 10, 30, tzinfo=TZ),
        acquired_at=acquired_at,
    )

    async def data_fetch(code, start, end, period, adjustment):
        return BarFetchBatch(
            (complete_bar,), acquired_at, "fixture", QualityStatus.OK, 1, 0
        )

    monkeypatch.setattr(fake_source, "fetch_minute_bars", data_fetch)
    service = BarService(
        fake_source,
        repository,
        history_days=60,
        now_provider=lambda: acquired_at,
    )

    await service._fetch_minute_provider(
        Market.SH,
        "600519",
        datetime(2026, 8, 4, 9, 30, tzinfo=TZ),
        acquired_at,
        "1m",
        "none",
    )

    with repository.database.lock:
        checked_text, expires_text = repository.database.connection.execute(
            "SELECT CAST(checked_at AS VARCHAR), CAST(expires_at AS VARCHAR) "
            "FROM bar_range_check"
        ).fetchone()
    assert datetime.fromisoformat(expires_text) - datetime.fromisoformat(
        checked_text
    ) <= timedelta(seconds=5)


async def test_60m_tail_refetches_late_bar_after_session_bucket_closes(
    repository, fake_source, monkeypatch
):
    clock = [datetime(2026, 8, 4, 11, 0, 1, tzinfo=TZ)]
    calls = 0
    first_end = datetime(2026, 8, 4, 10, 59, 59, tzinfo=TZ)
    late_bar = replace(
        fake_source.bar_rows[0],
        code="600519",
        market=Market.SH,
        period="60m",
        adjustment="qfq",
        bar_time=datetime(2026, 8, 4, 10, 30, tzinfo=TZ),
        acquired_at=datetime(2026, 8, 4, 11, 30, 1, tzinfo=TZ),
    )

    async def controlled_fetch(code, start, end, period, adjustment):
        nonlocal calls
        calls += 1
        bars = () if calls == 1 else (late_bar,)
        return BarFetchBatch(
            bars,
            clock[0],
            "fixture",
            QualityStatus.OK,
            len(bars),
            0,
        )

    monkeypatch.setattr(fake_source, "fetch_minute_bars", controlled_fetch)
    service = BarService(
        fake_source,
        repository,
        history_days=60,
        now_provider=lambda: clock[0],
    )

    await service._fetch_minute_provider(
        Market.SH,
        "600519",
        datetime(2026, 8, 4, 9, 30, tzinfo=TZ),
        first_end,
        "60m",
        "qfq",
    )
    with repository.database.lock:
        checked_text, expires_text = repository.database.connection.execute(
            "SELECT CAST(checked_at AS VARCHAR), CAST(expires_at AS VARCHAR) "
            "FROM bar_range_check WHERE range_end = ?",
            (first_end,),
        ).fetchone()
    first_ttl = datetime.fromisoformat(expires_text) - datetime.fromisoformat(
        checked_text
    )

    clock[0] = datetime(2026, 8, 4, 11, 30, 1, tzinfo=TZ)
    await service._fetch_minute_provider(
        Market.SH,
        "600519",
        datetime(2026, 8, 4, 10, 30, tzinfo=TZ),
        clock[0],
        "60m",
        "qfq",
    )
    saved = repository.get_bars(
        Market.SH,
        "600519",
        "60m",
        datetime(2026, 8, 4, 9, 30, tzinfo=TZ),
        clock[0],
        "qfq",
    )

    assert first_ttl <= timedelta(seconds=5)
    assert calls == 2
    assert [bar.bar_time for bar in saved] == [late_bar.bar_time]


@pytest.mark.parametrize("period", ["5m", "15m", "30m", "60m"])
@pytest.mark.parametrize(
    ("range_end", "acquired_at", "expected_long_ttl"),
    [
        (
            datetime(2026, 8, 4, 9, 30, 1, tzinfo=TZ),
            datetime(2026, 8, 4, 9, 30, 1, tzinfo=TZ),
            False,
        ),
        (
            datetime(2026, 8, 4, 11, 29, 59, tzinfo=TZ),
            datetime(2026, 8, 4, 11, 30, 1, tzinfo=TZ),
            True,
        ),
        (
            datetime(2026, 8, 4, 12, 0, tzinfo=TZ),
            datetime(2026, 8, 4, 12, 0, 1, tzinfo=TZ),
            True,
        ),
        (
            datetime(2026, 8, 4, 13, 0, 1, tzinfo=TZ),
            datetime(2026, 8, 4, 13, 0, 1, tzinfo=TZ),
            False,
        ),
        (
            datetime(2026, 8, 4, 14, 59, 59, tzinfo=TZ),
            datetime(2026, 8, 4, 15, 0, 1, tzinfo=TZ),
            True,
        ),
    ],
)
def test_minute_range_expiry_uses_a_share_trading_sessions(
    period,
    range_end,
    acquired_at,
    expected_long_ttl,
    repository,
    fake_source,
):
    service = BarService(
        fake_source,
        repository,
        history_days=60,
        query_ttl_seconds=2,
        range_recheck_seconds=7 * 24 * 60 * 60,
    )

    expires_at = service._range_expires_at(
        "minute",
        period,
        range_end,
        acquired_at,
        QualityStatus.OK,
    )

    expected_seconds = 7 * 24 * 60 * 60 if expected_long_ttl else 2
    assert expires_at - acquired_at == timedelta(seconds=expected_seconds)


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


async def test_akshare_stock_master_merges_exchange_listing_dates(monkeypatch):
    source = AkshareSource()
    monkeypatch.setattr(
        akshare_module.ak,
        "stock_info_sh_name_code",
        lambda symbol: pd.DataFrame(
            [
                {
                    "证券代码": "600519" if symbol == "主板A股" else "688001",
                    "证券简称": "贵州茅台" if symbol == "主板A股" else "华兴源创",
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
            [
                {
                    "A股代码": "000001",
                    "A股简称": "平安银行",
                    "A股上市日期": date(1991, 4, 3),
                }
            ]
        ),
    )
    monkeypatch.setattr(
        akshare_module.ak,
        "stock_info_bj_name_code",
        lambda: pd.DataFrame(
            [
                {
                    "证券代码": "920092",
                    "证券简称": "汉鑫科技",
                    "上市日期": date(2021, 11, 15),
                }
            ]
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
    checked_at = datetime(2026, 8, 4, 10, 0, tzinfo=TZ)
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE bar_range_check (
          market VARCHAR NOT NULL, code VARCHAR NOT NULL,
          period VARCHAR NOT NULL, adjustment VARCHAR NOT NULL,
          range_start TIMESTAMPTZ NOT NULL, range_end TIMESTAMPTZ NOT NULL,
          checked_at TIMESTAMPTZ NOT NULL, source VARCHAR NOT NULL,
          status VARCHAR NOT NULL, quality_status VARCHAR NOT NULL,
          raw_row_count BIGINT NOT NULL, valid_row_count BIGINT NOT NULL,
          invalid_row_count BIGINT NOT NULL,
          PRIMARY KEY (market, code, period, adjustment, range_start, range_end)
        )
        """
    )
    connection.execute(
        "INSERT INTO bar_range_check VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "SH",
            "600519",
            "1d",
            "qfq",
            datetime(2026, 7, 1, tzinfo=TZ),
            datetime(2026, 7, 31, 23, 59, 59, tzinfo=TZ),
            checked_at,
            "legacy",
            "success_empty",
            "ok",
            0,
            0,
            0,
        ),
    )
    connection.close()

    database = Database(path)
    try:
        columns = {
            row[1]
            for row in database.connection.execute(
                "PRAGMA table_info('bar_range_check')"
            ).fetchall()
        }
        migrated_expiry = datetime.fromisoformat(
            database.connection.execute(
                "SELECT CAST(expires_at AS VARCHAR) FROM bar_range_check"
            ).fetchone()[0]
        )
    finally:
        database.close()

    reopened = Database(path)
    reopened.close()

    assert {
        "market",
        "code",
        "period",
        "adjustment",
        "range_start",
        "range_end",
        "checked_at",
        "expires_at",
        "status",
        "quality_status",
    } <= columns
    assert migrated_expiry == checked_at


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
