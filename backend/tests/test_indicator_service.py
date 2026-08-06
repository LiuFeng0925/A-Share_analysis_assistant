from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from a_share_radar.domain.indicators import MacdQuality
from a_share_radar.domain.models import Bar, Market, QualityStatus, QuoteSnapshot, Stock
from a_share_radar.services.indicator_service import IndicatorService

TZ = ZoneInfo("Asia/Shanghai")


def daily_bar(index: int, close_price: float) -> Bar:
    bar_time = datetime.combine(date(2026, 6, 1) + timedelta(days=index), time(15, 0), tzinfo=TZ)
    return Bar(
        code="600519",
        market=Market.SH,
        period="1d",
        adjustment="qfq",
        bar_time=bar_time,
        open_price=close_price,
        high_price=close_price,
        low_price=close_price,
        close_price=close_price,
        volume=1_000_000,
        amount=close_price * 1_000_000,
        source="fixture",
        is_complete=True,
        acquired_at=bar_time,
        quality_status=QualityStatus.OK,
    )


def five_minute_bar(index: int, close_price: float) -> Bar:
    bar_time = datetime(2026, 7, 11, 9, 30, tzinfo=TZ) + timedelta(minutes=5 * index)
    return Bar(
        code="600519",
        market=Market.SH,
        period="5m",
        adjustment="qfq",
        bar_time=bar_time,
        open_price=close_price,
        high_price=close_price,
        low_price=close_price,
        close_price=close_price,
        volume=100_000,
        amount=close_price * 100_000,
        source="fixture",
        is_complete=True,
        acquired_at=bar_time,
        quality_status=QualityStatus.OK,
    )


def quote_snapshot(captured_at: datetime, latest_price: float) -> QuoteSnapshot:
    return QuoteSnapshot(
        code="600519",
        market=Market.SH,
        name="贵州茅台",
        captured_at=captured_at,
        latest_price=latest_price,
        change_percent=20.0,
        change_amount=2.0,
        open_price=10.0,
        high_price=latest_price,
        low_price=10.0,
        previous_close=10.0,
        volume=2_000_000,
        amount=latest_price * 2_000_000,
        turnover_rate=1.0,
        total_market_cap=100_000_000.0,
        source="fixture",
        quality_status=QualityStatus.PARTIAL,
    )


class RecordingBarService:
    def __init__(self, repository, bars: list[Bar]):
        self.repository = repository
        self.bars = bars
        self.calls: list[tuple[Market, str, str, str, str]] = []

    async def get_bars(
        self,
        market: Market,
        code: str,
        period: str,
        range_name: str,
        adjustment: str,
        now: datetime,
    ) -> list[Bar]:
        self.calls.append((market, code, period, range_name, adjustment))
        matching = [
            bar
            for bar in self.bars
            if bar.market == market
            and bar.code == code
            and bar.period == period
            and bar.adjustment == adjustment
        ]
        self.repository.upsert_bars(matching)
        return matching


async def test_indicator_service_refreshes_stock_macd_from_daily_bars_and_latest_quote(repository):
    now = datetime(2026, 7, 11, 10, 30, tzinfo=TZ)
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    repository.replace_trading_days({*(bar.bar_time.date() for bar in [daily_bar(i, 10.0) for i in range(40)]), now.date()})
    repository.upsert_bars([daily_bar(index, 10.0) for index in range(40)])
    repository.commit_snapshot_success(
        [quote_snapshot(now, 12.0)],
        started_at=now,
        source="fixture",
        market_time=now,
        expected_row_count=1,
        quality_status="partial",
    )
    stock = repository.get_stock(Market.SH, "600519")
    assert stock is not None

    await IndicatorService(repository).refresh_stock_macd(stock, now, market_open=True)

    calculation = repository.get_macd(Market.SH, "600519")
    assert calculation is not None
    assert calculation.summary.signal_type == "golden_cross"
    assert calculation.summary.recent_signal_label == "盘中金叉"
    assert calculation.summary.is_intraday is True


async def test_indicator_service_refreshes_stock_macd_for_requested_period(repository):
    now = datetime(2026, 7, 11, 11, 30, tzinfo=TZ)
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    repository.replace_trading_days({now.date()})
    bars = [five_minute_bar(index, 10.0) for index in range(40)]
    bars[-1] = replace(
        bars[-1],
        close_price=12.0,
        is_complete=False,
        quality_status=QualityStatus.PARTIAL,
    )
    repository.upsert_bars(bars)
    stock = repository.get_stock(Market.SH, "600519")
    assert stock is not None

    await IndicatorService(repository).refresh_stock_macd(
        stock,
        now,
        market_open=True,
        period="5m",
    )

    calculation = repository.get_macd(Market.SH, "600519", "5m")
    assert calculation is not None
    assert calculation.summary.period == "5m"
    assert calculation.summary.market_time == bars[-1].bar_time
    assert calculation.summary.quality == "partial"


async def test_indicator_service_get_stock_macd_fetches_period_bars_before_calculating(
    repository,
):
    now = datetime(2026, 7, 11, 11, 30, tzinfo=TZ)
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    repository.replace_trading_days({now.date()})
    bars = [five_minute_bar(index, 10.0 + index * 0.02) for index in range(40)]
    stock = repository.get_stock(Market.SH, "600519")
    assert stock is not None
    bar_service = RecordingBarService(repository, bars)

    calculation = await IndicatorService(repository, bar_service).get_stock_macd(
        stock,
        now,
        market_open=True,
        period="5m",
    )

    assert bar_service.calls == [(Market.SH, "600519", "5m", "6mo", "qfq")]
    assert calculation is not None
    assert calculation.summary.period == "5m"
    assert calculation.summary.quality is not MacdQuality.INSUFFICIENT
    assert calculation.summary.market_time == bars[-1].bar_time


async def test_indicator_service_get_stock_macd_refreshes_stale_cached_minute_result(
    repository,
):
    first_now = datetime(2026, 7, 11, 11, 30, tzinfo=TZ)
    second_now = datetime(2026, 7, 11, 11, 33, tzinfo=TZ)
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    repository.replace_trading_days({first_now.date()})
    bars = [five_minute_bar(index, 10.0 + index * 0.02) for index in range(40)]
    repository.upsert_bars(bars)
    stock = repository.get_stock(Market.SH, "600519")
    assert stock is not None
    service = IndicatorService(repository)
    await service.refresh_stock_macd(stock, first_now, market_open=True, period="5m")

    refreshed_tail = replace(
        bars[-1],
        close_price=13.0,
        acquired_at=first_now + timedelta(minutes=1),
    )
    repository.upsert_bars([refreshed_tail])

    calculation = await service.get_stock_macd(
        stock,
        second_now,
        market_open=True,
        period="5m",
    )

    assert calculation is not None
    assert calculation.summary.calculated_at == second_now
    assert calculation.summary.market_time == refreshed_tail.bar_time


async def test_indicator_service_get_stock_macd_refreshes_daily_when_intraday_quote_updates(
    repository,
):
    first_now = datetime(2026, 7, 11, 10, 30, tzinfo=TZ)
    second_now = datetime(2026, 7, 11, 10, 32, tzinfo=TZ)
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    bars = [daily_bar(index, 10.0) for index in range(40)]
    repository.replace_trading_days({*(bar.bar_time.date() for bar in bars), first_now.date()})
    repository.upsert_bars(bars)
    repository.commit_snapshot_success(
        [quote_snapshot(first_now, 12.0)],
        started_at=first_now,
        source="fixture",
        market_time=first_now,
        expected_row_count=1,
        quality_status="partial",
    )
    stock = repository.get_stock(Market.SH, "600519")
    assert stock is not None
    service = IndicatorService(repository)

    await service.get_stock_macd(stock, first_now, market_open=True, period="1d")
    repository.commit_snapshot_success(
        [quote_snapshot(first_now + timedelta(minutes=1), 12.5)],
        started_at=first_now + timedelta(minutes=1),
        source="fixture",
        market_time=first_now + timedelta(minutes=1),
        expected_row_count=1,
        quality_status="partial",
    )

    calculation = await service.get_stock_macd(
        stock,
        second_now,
        market_open=True,
        period="1d",
    )

    assert calculation is not None
    assert calculation.summary.calculated_at == second_now
