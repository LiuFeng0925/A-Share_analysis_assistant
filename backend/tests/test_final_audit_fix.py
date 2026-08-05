import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

import a_share_radar.data_sources.akshare_source as akshare_module
from a_share_radar.data_sources.akshare_source import AkshareSource
from a_share_radar.domain.models import Bar, BarFetchBatch, Market, QualityStatus, Stock
from a_share_radar.services.bar_service import BarService, HistoryBootstrapper

TZ = ZoneInfo("Asia/Shanghai")


async def _get(app, path: str, *, params: dict[str, str]):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, params=params)


def _trading_days_ending(end: date, count: int) -> list[date]:
    days: list[date] = []
    current = end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return sorted(days)


def _daily_bar(day: date, acquired_at: datetime, close_price: float = 10.2) -> Bar:
    return Bar(
        code="600519",
        market=Market.SH,
        period="1d",
        adjustment="qfq",
        bar_time=datetime.combine(day, time(15, 0), tzinfo=TZ),
        open_price=10.1,
        high_price=max(10.3, close_price),
        low_price=10.0,
        close_price=close_price,
        volume=100_000,
        amount=1_020_000.0,
        source="fixture",
        acquired_at=acquired_at,
        is_complete=True,
        quality_status=QualityStatus.OK,
    )


async def test_today_daily_bar_stays_partial_until_1520_and_is_then_overwritten(
    repository, fake_source, monkeypatch
):
    trading_days = _trading_days_ending(date(2026, 8, 4), 60)
    repository.replace_trading_days(set(trading_days))
    earlier_days = trading_days[:-1]
    repository.upsert_bars(
        [
            _daily_bar(
                day,
                datetime(2026, 8, 4, 15, 15, tzinfo=TZ),
            )
            for day in earlier_days
        ]
    )
    clock = [datetime(2026, 8, 4, 15, 15, tzinfo=TZ)]
    calls: list[tuple[date, date]] = []

    async def staged_fetch(code, start, end, period, adjustment):
        calls.append((start, end))
        close_price = 10.2 if len(calls) == 1 else 10.8
        bar = _daily_bar(date(2026, 8, 4), clock[0], close_price)
        return BarFetchBatch(
            (bar,), clock[0], "fixture", QualityStatus.OK, 1, 0
        )

    monkeypatch.setattr(fake_source, "fetch_daily_bars", staged_fetch)
    service = BarService(
        fake_source,
        repository,
        history_days=60,
        now_provider=lambda: clock[0],
    )
    stock = Stock("600519", Market.SH, "贵州茅台")

    provisional = await service.ensure_daily_history(stock, date(2026, 8, 4))

    assert provisional[-1].bar_time.date() == date(2026, 8, 4)
    assert provisional[-1].is_complete is False
    assert provisional[-1].quality_status is QualityStatus.PARTIAL
    assert HistoryBootstrapper._latest_completed_day(
        clock[0], set(trading_days)
    ) == trading_days[-2]

    clock[0] = datetime(2026, 8, 4, 15, 20, tzinfo=TZ)
    formal = await service.ensure_daily_history(stock, date(2026, 8, 4))

    assert calls == [
        (date(2026, 8, 4), date(2026, 8, 4)),
        (date(2026, 8, 4), date(2026, 8, 4)),
    ]
    assert formal[-1].close_price == 10.8
    assert formal[-1].is_complete is True
    assert formal[-1].quality_status is QualityStatus.OK
    assert HistoryBootstrapper._latest_completed_day(
        clock[0], set(trading_days)
    ) == date(2026, 8, 4)


@pytest.mark.parametrize(
    ("period", "bar_time", "acquired_at", "expected_complete"),
    [
        (
            "1m",
            datetime(2026, 8, 4, 9, 31, tzinfo=TZ),
            datetime(2026, 8, 4, 9, 31, 10, tzinfo=TZ),
            True,
        ),
        (
            "5m",
            datetime(2026, 8, 4, 11, 30, tzinfo=TZ),
            datetime(2026, 8, 4, 11, 30, 10, tzinfo=TZ),
            True,
        ),
        (
            "15m",
            datetime(2026, 8, 4, 12, 0, tzinfo=TZ),
            datetime(2026, 8, 4, 12, 5, tzinfo=TZ),
            False,
        ),
        (
            "60m",
            datetime(2026, 8, 4, 15, 0, tzinfo=TZ),
            datetime(2026, 8, 4, 15, 0, 10, tzinfo=TZ),
            True,
        ),
        (
            "30m",
            datetime(2026, 8, 4, 14, 30, tzinfo=TZ),
            datetime(2026, 8, 4, 14, 29, 59, tzinfo=TZ),
            False,
        ),
        (
            "60m",
            datetime(2026, 8, 4, 10, 30, tzinfo=TZ),
            datetime(2026, 8, 4, 11, 35, tzinfo=TZ),
            True,
        ),
    ],
)
def test_minute_bar_uses_eastmoney_period_end_label(
    period, bar_time, acquired_at, expected_complete
):
    frame = pd.DataFrame(
        [
            {
                "时间": bar_time.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                "开盘": 10.1,
                "最高": 10.3,
                "最低": 10.0,
                "收盘": 10.2,
                "成交量": 1_000,
                "成交额": 1_020_000.0,
            }
        ]
    )

    source_bar = AkshareSource.normalize_minute_bars(
        "600519", frame, period, "qfq", acquired_at=acquired_at
    )[0]
    service_bar = BarService._mark_completion(
        [replace(source_bar, is_complete=not expected_complete)]
    )[0]

    assert source_bar.is_complete is expected_complete
    assert service_bar.is_complete is expected_complete


async def test_unknown_stock_is_404_and_has_zero_side_effects(
    app_with_fixture_data, fake_source, monkeypatch
):
    repository = app_with_fixture_data.state.repository
    service = app_with_fixture_data.state.bar_service
    upstream_calls = 0

    async def forbidden_fetch(*args, **kwargs):
        nonlocal upstream_calls
        upstream_calls += 1
        return []

    monkeypatch.setattr(fake_source, "fetch_minute_bars", forbidden_fetch)
    with repository.database.lock:
        before = repository.database.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM bar_hot), "
            "(SELECT COUNT(*) FROM ingestion_run WHERE kind = 'bar'), "
            "(SELECT COUNT(*) FROM bar_range_check)"
        ).fetchone()

    response = await _get(
        app_with_fixture_data,
        "/api/stocks/SH/999999/bars",
        params={"period": "1m", "range": "today", "adjustment": "none"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "未找到该股票"}
    assert upstream_calls == 0

    with pytest.raises(LookupError, match="未找到该股票"):
        await service.get_bars(
            Market.SH,
            "999999",
            "1m",
            "today",
            "none",
            datetime(2026, 8, 4, 10, 31, tzinfo=TZ),
        )

    with repository.database.lock:
        after = repository.database.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM bar_hot), "
            "(SELECT COUNT(*) FROM ingestion_run WHERE kind = 'bar'), "
            "(SELECT COUNT(*) FROM bar_range_check)"
        ).fetchone()
    assert upstream_calls == 0
    assert after == before


async def test_wide_daily_success_only_confirms_returned_days_then_checks_missing_day(
    repository, fake_source, monkeypatch
):
    trading_days = _trading_days_ending(date(2026, 8, 4), 60)
    missing_day = trading_days[27]
    repository.replace_trading_days(set(trading_days))
    calls: list[tuple[date, date]] = []
    acquired_at = datetime(2026, 8, 5, 9, 0, tzinfo=TZ)

    async def gap_then_empty(code, start, end, period, adjustment):
        calls.append((start, end))
        bars = (
            tuple(
                _daily_bar(day, acquired_at)
                for day in trading_days
                if day != missing_day
            )
            if len(calls) == 1
            else ()
        )
        return BarFetchBatch(
            bars, acquired_at, "fixture", QualityStatus.OK, len(bars), 0
        )

    monkeypatch.setattr(fake_source, "fetch_daily_bars", gap_then_empty)
    service = BarService(
        fake_source,
        repository,
        history_days=60,
        now_provider=lambda: acquired_at,
    )
    stock = Stock("600519", Market.SH, "贵州茅台")

    first = await service.ensure_daily_history(stock, trading_days[-1])
    second = await service.ensure_daily_history(stock, trading_days[-1])
    third = await service.ensure_daily_history(stock, trading_days[-1])

    assert len(first) == 59
    assert len(second) == 59
    assert len(third) == 59
    assert calls == [
        (trading_days[0], trading_days[-1]),
        (missing_day, missing_day),
    ]
    with repository.database.lock:
        statuses = repository.database.connection.execute(
            "SELECT status, CAST(range_start AS DATE), CAST(range_end AS DATE) "
            "FROM bar_range_check ORDER BY range_start"
        ).fetchall()
    assert ("success_empty", missing_day, missing_day) in statuses
    assert ("success", trading_days[0], trading_days[-1]) not in statuses


async def test_stock_master_refresh_never_consumes_realtime_spot(monkeypatch):
    spot_calls = 0

    def spot_provider():
        nonlocal spot_calls
        spot_calls += 1
        return pd.DataFrame(
            [{"代码": "600519", "名称": "贵州茅台", "最新价": 10.2}]
        )

    monkeypatch.setattr(akshare_module.ak, "stock_zh_a_spot_em", spot_provider)
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
    source = AkshareSource()

    first = await source.fetch_stock_master()
    second = await source.fetch_stock_master()
    await source.fetch_market_snapshot()

    assert spot_calls == 1
    assert second == first
    assert {(stock.code, stock.name, stock.list_date) for stock in first} == {
        ("600519", "贵州茅台", date(2001, 8, 27)),
        ("688001", "华兴源创", date(2019, 7, 22)),
        ("000001", "平安银行", date(1991, 4, 3)),
        ("920092", "汉鑫科技", date(2021, 11, 15)),
    }


async def test_akshare_listing_frame_times_out_instead_of_blocking_startup(monkeypatch):
    async def never_returns(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(akshare_module, "_run_provider_thread", never_returns)

    frame = await AkshareSource._fetch_listing_frame(
        "上交所主板", lambda: None, timeout_seconds=0.01
    )

    assert frame.empty


async def test_akshare_one_minute_opening_batch_marks_0930_and_0931_complete(
    monkeypatch,
):
    frame = pd.DataFrame(
        [
            {
                "时间": label,
                "开盘": 10.1,
                "最高": 10.3,
                "最低": 10.0,
                "收盘": 10.2,
                "成交量": 1_000,
                "成交额": 1_020_000.0,
            }
            for label in ("2026-08-04 09:30:00", "2026-08-04 09:31:00")
        ]
    )

    def provider(**kwargs):
        return frame

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 4, 9, 32, tzinfo=tz)

    monkeypatch.setattr(
        akshare_module.ak, "stock_zh_a_hist_min_em", provider
    )
    monkeypatch.setattr(akshare_module, "datetime", FixedDateTime)

    batch = await AkshareSource().fetch_minute_bars(
        "600519",
        datetime(2026, 8, 4, 9, 30, tzinfo=TZ),
        datetime(2026, 8, 4, 9, 32, tzinfo=TZ),
        "1m",
        "none",
    )

    assert [bar.bar_time.time() for bar in batch.bars] == [time(9, 30), time(9, 31)]
    assert [bar.is_complete for bar in batch.bars] == [True, True]
    assert [bar.quality_status for bar in batch.bars] == [
        QualityStatus.OK,
        QualityStatus.OK,
    ]
    assert batch.quality_status is QualityStatus.OK
