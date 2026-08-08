from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from a_share_radar.domain.indicators import (
    KdjCalculation,
    KdjPoint,
    KdjQuality,
    KdjSignal,
    KdjSignalZone,
    KdjSummary,
    KdjZone,
)
from a_share_radar.domain.models import Bar, QualityStatus
from a_share_radar.services.macd import SHANGHAI, _identity, _period_bars, _with_intraday_quote

MINIMUM_KDJ_BARS = 9


def calculate_kdj_series(
    bars: Sequence[Bar],
    *,
    latest_quote: Any | None,
    trading_days: Sequence[date],
    now: datetime,
    market_open: bool,
    period: str = "1d",
) -> KdjCalculation:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("KDJ 计算时间必须包含时区")
    selected_bars = _period_bars(bars, period)
    market, code = _identity(selected_bars, latest_quote)
    if period == "1d":
        calculation_bars, has_intraday = _with_intraday_quote(
            selected_bars, latest_quote, now.astimezone(SHANGHAI), market_open
        )
    else:
        calculation_bars = selected_bars
        has_intraday = bool(
            calculation_bars
            and (
                not calculation_bars[-1].is_complete
                or calculation_bars[-1].quality_status is QualityStatus.PARTIAL
            )
        )
    has_partial_data = any(
        not item.is_complete or item.quality_status is not QualityStatus.OK
        for item in calculation_bars
    )
    quality = KdjQuality.PARTIAL if has_partial_data else KdjQuality.OK
    if len(calculation_bars) < MINIMUM_KDJ_BARS:
        return _insufficient_calculation(
            market=market,
            code=code,
            period=period,
            calculated_at=now,
            market_time=calculation_bars[-1].bar_time if calculation_bars else None,
            is_intraday=has_intraday,
        )

    previous_k = 50.0
    previous_d = 50.0
    previous_point: KdjPoint | None = None
    points: list[KdjPoint] = []
    for index in range(MINIMUM_KDJ_BARS - 1, len(calculation_bars)):
        item = calculation_bars[index]
        window = calculation_bars[index - MINIMUM_KDJ_BARS + 1 : index + 1]
        highest = max(window_item.high_price for window_item in window)
        lowest = min(window_item.low_price for window_item in window)
        rsv = 50.0 if highest == lowest else (item.close_price - lowest) / (highest - lowest) * 100
        k_value = previous_k * 2 / 3 + rsv / 3
        d_value = previous_d * 2 / 3 + k_value / 3
        j_value = 3 * k_value - 2 * d_value
        signal_type = _signal(previous_point, k_value, d_value)
        is_intraday = has_intraday and index == len(calculation_bars) - 1
        point = KdjPoint(
            market=item.market,
            code=item.code,
            period=period,
            bar_time=item.bar_time,
            k_value=k_value,
            d_value=d_value,
            j_value=j_value,
            signal_type=signal_type,
            signal_zone=(
                _classify_signal_zone(k_value, d_value)
                if signal_type is not KdjSignal.NONE
                else KdjSignalZone.UNKNOWN
            ),
            current_zone=_classify_zone(k_value, d_value),
            is_intraday=is_intraday,
            quality=quality,
        )
        points.append(point)
        previous_point = point
        previous_k = k_value
        previous_d = d_value

    latest = points[-1]
    recent_signal = next(
        (point for point in reversed(points) if point.signal_type is not KdjSignal.NONE),
        None,
    )
    recent_signal_days = _recent_signal_days(
        recent_signal,
        trading_days,
        latest.bar_time.date(),
        [item.bar_time.date() for item in calculation_bars],
    )
    summary_quality = (
        KdjQuality.PARTIAL
        if recent_signal is not None
        and recent_signal.bar_time.date() != latest.bar_time.date()
        and recent_signal_days is None
        else quality
    )
    return KdjCalculation(
        summary=KdjSummary(
            market=latest.market,
            code=latest.code,
            period=period,
            calculated_at=now,
            market_time=latest.bar_time,
            k_value=latest.k_value,
            d_value=latest.d_value,
            j_value=latest.j_value,
            current_zone=latest.current_zone,
            signal_type=(KdjSignal.NONE if recent_signal is None else recent_signal.signal_type),
            signal_time=None if recent_signal is None else recent_signal.bar_time,
            signal_zone=(
                KdjSignalZone.UNKNOWN if recent_signal is None else recent_signal.signal_zone
            ),
            recent_signal_days=recent_signal_days,
            recent_signal_label=_signal_label(recent_signal, recent_signal_days, market_open),
            status=_status(latest),
            is_intraday=latest.is_intraday,
            quality=summary_quality,
        ),
        points=tuple(points),
    )


def _insufficient_calculation(
    *, market, code: str, period: str, calculated_at: datetime,
    market_time: datetime | None, is_intraday: bool,
) -> KdjCalculation:
    return KdjCalculation(
        summary=KdjSummary(
            market=market,
            code=code,
            period=period,
            calculated_at=calculated_at,
            market_time=market_time,
            k_value=None,
            d_value=None,
            j_value=None,
            current_zone=KdjZone.UNKNOWN,
            signal_type=KdjSignal.NONE,
            signal_time=None,
            signal_zone=KdjSignalZone.UNKNOWN,
            recent_signal_days=None,
            recent_signal_label="数据不足",
            status="insufficient",
            is_intraday=is_intraday,
            quality=KdjQuality.INSUFFICIENT,
        ),
        points=(),
    )


def _signal(previous: KdjPoint | None, k_value: float, d_value: float) -> KdjSignal:
    if previous is None or previous.k_value is None or previous.d_value is None:
        return KdjSignal.NONE
    if previous.k_value <= previous.d_value and k_value > d_value:
        return KdjSignal.GOLDEN_CROSS
    if previous.k_value >= previous.d_value and k_value < d_value:
        return KdjSignal.DEATH_CROSS
    return KdjSignal.NONE


def _classify_zone(k_value: float, d_value: float) -> KdjZone:
    if k_value <= 20 and d_value <= 20:
        return KdjZone.OVERSOLD
    if k_value >= 80 and d_value >= 80:
        return KdjZone.OVERBOUGHT
    return KdjZone.NEUTRAL


def _classify_signal_zone(k_value: float, d_value: float) -> KdjSignalZone:
    zone = _classify_zone(k_value, d_value)
    if zone is KdjZone.OVERSOLD:
        return KdjSignalZone.LOW
    if zone is KdjZone.OVERBOUGHT:
        return KdjSignalZone.HIGH
    return KdjSignalZone.MIDDLE


def _recent_signal_days(
    point: KdjPoint | None,
    trading_days: Sequence[date],
    latest_date: date,
    observed_bar_days: Sequence[date],
) -> int | None:
    if point is None:
        return None
    signal_date = point.bar_time.date()
    if signal_date == latest_date:
        return 0
    known_days = set(trading_days)
    if signal_date not in known_days or latest_date not in known_days:
        return None
    observed_interval = {
        observed_day
        for observed_day in observed_bar_days
        if signal_date <= observed_day <= latest_date
    }
    if not observed_interval.issubset(known_days):
        return None
    ordered_days = sorted(known_days)
    positions = {trading_day: index for index, trading_day in enumerate(ordered_days)}
    distance = positions[latest_date] - positions[signal_date]
    return distance if distance >= 0 else None


def _signal_label(
    point: KdjPoint | None, recent_signal_days: int | None, market_open: bool
) -> str:
    if point is None:
        return "暂无"
    signal_name = "金叉" if point.signal_type is KdjSignal.GOLDEN_CROSS else "死叉"
    if recent_signal_days == 0:
        return f"盘中{signal_name}" if point.is_intraday and market_open else f"今日{signal_name}"
    if recent_signal_days is not None and recent_signal_days <= 2:
        return f"近 3 日{signal_name}"
    if recent_signal_days is not None and recent_signal_days <= 4:
        return f"近 5 日{signal_name}"
    return f"{point.bar_time.astimezone(SHANGHAI):%Y-%m-%d} {signal_name}"


def _status(point: KdjPoint) -> str:
    if point.signal_type is KdjSignal.GOLDEN_CROSS:
        return "golden_after"
    if point.signal_type is KdjSignal.DEATH_CROSS:
        return "death_after"
    if point.k_value is not None and point.d_value is not None and point.k_value > point.d_value:
        return "bullish"
    if point.k_value is not None and point.d_value is not None and point.k_value < point.d_value:
        return "bearish"
    return "neutral"
