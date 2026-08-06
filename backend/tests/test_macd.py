from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from a_share_radar.domain.models import Bar, Market, QualityStatus
from a_share_radar.services.macd import calculate_macd_series
from a_share_radar.storage.repository import StockQuoteRow

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


def quote(latest_price: float, captured_at: datetime) -> StockQuoteRow:
    return StockQuoteRow(
        code="600519",
        market=Market.SH,
        name="贵州茅台",
        list_status="L",
        list_date=None,
        captured_at=captured_at,
        latest_price=latest_price,
        change_percent=10.0,
        change_amount=latest_price - 10.0,
        open_price=10.0,
        high_price=max(10.0, latest_price),
        low_price=min(10.0, latest_price),
        previous_close=10.0,
        volume=2_000_000,
        amount=latest_price * 2_000_000,
        turnover_rate=1.0,
        total_market_cap=100_000_000,
        source="fixture",
        quality_status=QualityStatus.PARTIAL,
    )


def test_intraday_latest_price_can_create_today_golden_cross():
    bars = [daily_bar(index, 10.0) for index in range(40)]
    now = datetime(2026, 7, 11, 10, 30, tzinfo=TZ)
    trading_days = [bar.bar_time.date() for bar in bars] + [now.date()]

    result = calculate_macd_series(
        bars,
        latest_quote=quote(12.0, now),
        trading_days=trading_days,
        now=now,
        market_open=True,
    )

    assert result.summary.signal_type == "golden_cross"
    assert result.summary.zero_axis == "above"
    assert result.summary.recent_signal_label == "盘中金叉"
    assert result.summary.recent_signal_days == 0
    assert result.summary.is_intraday is True
    assert result.summary.quality == "partial"
    assert result.points[-1].signal_type == "golden_cross"
    assert result.points[-1].diff is not None
    assert result.points[-1].dea is not None
    assert result.points[-1].histogram is not None


def test_intraday_latest_price_can_create_today_death_cross_below_zero():
    bars = [daily_bar(index, 10.0) for index in range(40)]
    now = datetime(2026, 7, 11, 10, 30, tzinfo=TZ)
    trading_days = [bar.bar_time.date() for bar in bars] + [now.date()]

    result = calculate_macd_series(
        bars,
        latest_quote=quote(8.0, now),
        trading_days=trading_days,
        now=now,
        market_open=True,
    )

    assert result.summary.signal_type == "death_cross"
    assert result.summary.zero_axis == "below"
    assert result.summary.recent_signal_label == "盘中死叉"
    assert result.points[-1].histogram is not None
    assert result.points[-1].histogram < 0


def test_macd_marks_insufficient_history_without_signal():
    bars = [daily_bar(index, 10.0) for index in range(20)]
    now = datetime(2026, 7, 11, 10, 30, tzinfo=TZ)

    result = calculate_macd_series(
        bars,
        latest_quote=quote(12.0, now),
        trading_days=[bar.bar_time.date() for bar in bars] + [now.date()],
        now=now,
        market_open=True,
    )

    assert result.summary.quality == "insufficient"
    assert result.summary.signal_type == "none"
    assert result.summary.recent_signal_label == "数据不足"
    assert result.points == ()
