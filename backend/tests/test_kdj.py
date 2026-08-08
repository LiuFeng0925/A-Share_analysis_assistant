from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from a_share_radar.domain.indicators import KdjQuality, KdjSignal, KdjSignalZone, KdjZone
from a_share_radar.domain.models import Bar, Market, QualityStatus
from a_share_radar.services.kdj import calculate_kdj_series
from a_share_radar.storage.repository import StockQuoteRow

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 11, 10, 30, tzinfo=TZ)


def bar(
    index: int,
    close_price: float,
    *,
    low_price: float = 0.0,
    high_price: float = 100.0,
    period: str = "1d",
) -> Bar:
    bar_time = datetime.combine(
        date(2026, 6, 1) + timedelta(days=index), time(15, 0), tzinfo=TZ
    )
    return Bar(
        code="600519",
        market=Market.SH,
        period=period,
        adjustment="qfq",
        bar_time=bar_time,
        open_price=close_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=1_000_000,
        amount=close_price * 1_000_000,
        source="fixture",
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


def calculate(closes: list[float], *, period: str = "1d"):
    bars = [bar(index, close, period=period) for index, close in enumerate(closes)]
    return calculate_kdj_series(
        bars,
        latest_quote=None,
        trading_days=[item.bar_time.date() for item in bars],
        now=NOW,
        market_open=False,
        period=period,
    )


def test_kdj_uses_50_seed_and_does_not_emit_signal_on_first_valid_point():
    bars = [bar(index, 10.0, low_price=10.0, high_price=10.0) for index in range(8)]
    bars.append(bar(8, 12.0, low_price=10.0, high_price=12.0))

    result = calculate_kdj_series(
        bars,
        latest_quote=None,
        trading_days=[item.bar_time.date() for item in bars],
        now=NOW,
        market_open=False,
    )

    assert len(result.points) == 1
    assert result.points[0].k_value == pytest.approx(66.666667)
    assert result.points[0].d_value == pytest.approx(55.555556)
    assert result.points[0].j_value == pytest.approx(88.888889)
    assert result.points[0].signal_type is KdjSignal.NONE


def test_kdj_returns_insufficient_without_nine_matching_bars():
    result = calculate([10.0] * 8)

    assert result.points == ()
    assert result.summary.quality is KdjQuality.INSUFFICIENT
    assert result.summary.signal_type is KdjSignal.NONE
    assert result.summary.recent_signal_label == "数据不足"


def test_kdj_uses_neutral_rsv_when_window_high_equals_low():
    bars = [bar(index, 10.0, low_price=10.0, high_price=10.0) for index in range(9)]

    result = calculate_kdj_series(
        bars,
        latest_quote=None,
        trading_days=[item.bar_time.date() for item in bars],
        now=NOW,
        market_open=False,
    )

    assert result.points[0].k_value == pytest.approx(50.0)
    assert result.points[0].d_value == pytest.approx(50.0)
    assert result.points[0].j_value == pytest.approx(50.0)


def test_kdj_records_low_golden_cross_once_and_preserves_signal_time():
    result = calculate([0.0] * 22 + [35.0, 40.0])
    golden_points = [
        point for point in result.points if point.signal_type is KdjSignal.GOLDEN_CROSS
    ]

    assert len(golden_points) == 1
    assert golden_points[0].signal_zone is KdjSignalZone.LOW
    assert result.summary.signal_type is KdjSignal.GOLDEN_CROSS
    assert result.summary.signal_time == golden_points[0].bar_time
    assert result.summary.current_zone is KdjZone.NEUTRAL


def test_kdj_records_high_death_cross():
    result = calculate([100.0] * 22 + [65.0])
    death_points = [
        point for point in result.points if point.signal_type is KdjSignal.DEATH_CROSS
    ]

    assert len(death_points) == 1
    assert death_points[0].signal_zone is KdjSignalZone.HIGH
    assert result.summary.signal_type is KdjSignal.DEATH_CROSS


def test_kdj_does_not_clamp_extreme_j_values():
    result = calculate([0.0] * 22 + [100.0, 100.0, 100.0])

    assert any(point.j_value is not None and point.j_value < 0 for point in result.points)
    assert any(point.j_value is not None and point.j_value > 100 for point in result.points)


def test_kdj_calculates_only_requested_period_and_marks_dynamic_tail_partial():
    other_period = [bar(index, 99.0, period="1d") for index in range(12)]
    minute_bars = [bar(index, 20.0 + index, period="30m") for index in range(12)]
    minute_bars[-1] = replace(
        minute_bars[-1], is_complete=False, quality_status=QualityStatus.PARTIAL
    )

    result = calculate_kdj_series(
        [*other_period, *minute_bars],
        latest_quote=quote(1.0, NOW),
        trading_days=[NOW.date()],
        now=NOW,
        market_open=True,
        period="30m",
    )

    assert result.summary.period == "30m"
    assert result.summary.market_time == minute_bars[-1].bar_time
    assert result.summary.is_intraday is True
    assert result.summary.quality is KdjQuality.PARTIAL
    assert all(point.period == "30m" for point in result.points)
    assert result.points[-1].is_intraday is True


def test_kdj_daily_view_uses_latest_quote_as_provisional_bar():
    historical = [bar(index, 0.0) for index in range(22)]
    current_quote = quote(35.0, NOW)

    result = calculate_kdj_series(
        historical,
        latest_quote=current_quote,
        trading_days=[*[item.bar_time.date() for item in historical], NOW.date()],
        now=NOW,
        market_open=True,
        period="1d",
    )

    assert result.summary.market_time == datetime(2026, 7, 11, 15, 0, tzinfo=TZ)
    assert result.summary.is_intraday is True
    assert result.summary.quality is KdjQuality.PARTIAL
    assert result.points[-1].signal_type is KdjSignal.GOLDEN_CROSS
    assert result.summary.recent_signal_label == "盘中金叉"


def test_kdj_does_not_guess_recent_window_when_trading_calendar_is_incomplete():
    bars = [bar(index, close) for index, close in enumerate([0.0] * 22 + [35.0, 40.0])]

    result = calculate_kdj_series(
        bars,
        latest_quote=None,
        trading_days=[],
        now=NOW,
        market_open=False,
    )

    assert result.summary.signal_type is KdjSignal.GOLDEN_CROSS
    assert result.summary.recent_signal_days is None
    assert result.summary.recent_signal_label.endswith("金叉")
    assert result.summary.recent_signal_label.startswith("2026-")
    assert result.summary.quality is KdjQuality.PARTIAL


def test_kdj_rejects_calendar_with_endpoints_but_missing_observed_middle_day():
    bars = [
        bar(index, close)
        for index, close in enumerate([0.0] * 22 + [35.0, 40.0, 40.0])
    ]
    signal_date = bars[-3].bar_time.date()
    latest_date = bars[-1].bar_time.date()
    incomplete_calendar = [signal_date, latest_date]

    result = calculate_kdj_series(
        bars,
        latest_quote=None,
        trading_days=incomplete_calendar,
        now=NOW,
        market_open=False,
    )

    assert result.summary.signal_type is KdjSignal.GOLDEN_CROSS
    assert result.summary.recent_signal_days is None
    assert result.summary.quality is KdjQuality.PARTIAL
