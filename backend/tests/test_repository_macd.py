from datetime import date, datetime
from zoneinfo import ZoneInfo

from a_share_radar.domain.indicators import (
    MacdCalculation,
    MacdPoint,
    MacdQuality,
    MacdSignal,
    MacdSummary,
    ZeroAxisPosition,
)
from a_share_radar.domain.models import Market, Stock

TZ = ZoneInfo("Asia/Shanghai")


def macd_calculation(
    code: str,
    *,
    signal: MacdSignal,
    zero_axis: ZeroAxisPosition,
    days: int | None,
    label: str,
    period: str = "1d",
) -> MacdCalculation:
    market_time = datetime(2026, 8, 6, 15, 0, tzinfo=TZ)
    signal_date = None if days is None else date(2026, 8, 6)
    summary = MacdSummary(
        market=Market.SH,
        code=code,
        period=period,
        calculated_at=datetime(2026, 8, 6, 15, 5, tzinfo=TZ),
        market_time=market_time,
        diff=0.18,
        dea=0.11,
        histogram=0.14,
        signal_type=signal,
        signal_date=signal_date,
        recent_signal_days=days,
        recent_signal_label=label,
        zero_axis=zero_axis,
        status="golden_after" if signal is MacdSignal.GOLDEN_CROSS else "bearish",
        is_intraday=False,
        quality=MacdQuality.OK,
    )
    point = MacdPoint(
        market=Market.SH,
        code=code,
        period=period,
        bar_time=market_time,
        diff=summary.diff,
        dea=summary.dea,
        histogram=summary.histogram,
        signal_type=signal,
        zero_axis=zero_axis,
        is_intraday=False,
        quality=MacdQuality.OK,
    )
    return MacdCalculation(summary=summary, points=(point,))


def test_repository_saves_and_loads_macd_calculation(repository):
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    calculation = macd_calculation(
        "600519",
        signal=MacdSignal.GOLDEN_CROSS,
        zero_axis=ZeroAxisPosition.ABOVE,
        days=2,
        label="近 3 日金叉",
    )

    repository.upsert_macd(calculation)

    stored = repository.get_macd(Market.SH, "600519")
    assert stored is not None
    assert stored.summary.signal_type is MacdSignal.GOLDEN_CROSS
    assert stored.summary.zero_axis is ZeroAxisPosition.ABOVE
    assert stored.summary.recent_signal_label == "近 3 日金叉"
    assert stored.points[0].histogram == 0.14


def test_list_stocks_filters_and_returns_recent_macd_signal(repository):
    repository.upsert_stocks(
        [
            Stock("600519", Market.SH, "贵州茅台"),
            Stock("601318", Market.SH, "中国平安"),
        ]
    )
    repository.upsert_macd(
        macd_calculation(
            "600519",
            signal=MacdSignal.GOLDEN_CROSS,
            zero_axis=ZeroAxisPosition.ABOVE,
            days=2,
            label="近 3 日金叉",
        )
    )
    repository.upsert_macd(
        macd_calculation(
            "601318",
            signal=MacdSignal.DEATH_CROSS,
            zero_axis=ZeroAxisPosition.BELOW,
            days=4,
            label="近 5 日死叉",
        )
    )

    page = repository.list_stocks(
        None,
        None,
        "code",
        "asc",
        1,
        50,
        macd_signal="golden_cross",
        macd_zero_axis="above",
        macd_recent_window="3d",
    )

    assert page.total == 1
    assert page.items[0].code == "600519"
    assert page.items[0].macd_signal_type == MacdSignal.GOLDEN_CROSS
    assert page.items[0].macd_signal_label == "近 3 日金叉"
    assert page.items[0].macd_zero_axis == ZeroAxisPosition.ABOVE
    assert page.items[0].macd_quality == MacdQuality.OK


def test_list_stocks_ignores_non_daily_macd_for_filters(repository):
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    repository.upsert_macd(
        macd_calculation(
            "600519",
            signal=MacdSignal.GOLDEN_CROSS,
            zero_axis=ZeroAxisPosition.ABOVE,
            days=0,
            label="今日金叉",
            period="5m",
        )
    )

    page = repository.list_stocks(
        None,
        None,
        "code",
        "asc",
        1,
        50,
        macd_signal="golden_cross",
        macd_zero_axis="above",
        macd_recent_window="today",
    )

    assert page.total == 0
