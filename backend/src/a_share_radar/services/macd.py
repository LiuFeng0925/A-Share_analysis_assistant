from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from a_share_radar.domain.indicators import (
    MacdCalculation,
    MacdPoint,
    MacdQuality,
    MacdSignal,
    MacdSummary,
    ZeroAxisPosition,
)
from a_share_radar.domain.models import Bar, Market, QualityStatus
from a_share_radar.services.macd_divergence import calculate_macd_divergences

SHANGHAI = ZoneInfo("Asia/Shanghai")
MINIMUM_MACD_BARS = 35


def calculate_macd_series(
    bars: Sequence[Bar],
    *,
    latest_quote: Any | None,
    trading_days: Sequence[date],
    now: datetime,
    market_open: bool,
    period: str = "1d",
) -> MacdCalculation:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("MACD 计算时间必须包含时区")
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
    quality = MacdQuality.PARTIAL if has_intraday else MacdQuality.OK
    if len(calculation_bars) < MINIMUM_MACD_BARS:
        return MacdCalculation(
            summary=MacdSummary(
                market=market,
                code=code,
                period=period,
                calculated_at=now,
                market_time=calculation_bars[-1].bar_time if calculation_bars else None,
                diff=None,
                dea=None,
                histogram=None,
                signal_type=MacdSignal.NONE,
                signal_date=None,
                recent_signal_days=None,
                recent_signal_label="数据不足",
                zero_axis=ZeroAxisPosition.UNKNOWN,
                status="insufficient",
                is_intraday=has_intraday,
                quality=MacdQuality.INSUFFICIENT,
            ),
            points=(),
        )

    closes = [bar.close_price for bar in calculation_bars]
    diffs = [
        fast - slow
        for fast, slow in zip(_ema(closes, 12), _ema(closes, 26), strict=True)
    ]
    deas = _ema(diffs, 9)
    histograms = [2 * (diff - dea) for diff, dea in zip(diffs, deas, strict=True)]
    signal_types = _signals(diffs, deas)
    points = tuple(
        MacdPoint(
            market=bar.market,
            code=bar.code,
            period=period,
            bar_time=bar.bar_time,
            diff=diff,
            dea=dea,
            histogram=histogram,
            signal_type=signal,
            zero_axis=_zero_axis(diff),
            is_intraday=(has_intraday and index == len(calculation_bars) - 1),
            quality=quality,
        )
        for index, (bar, diff, dea, histogram, signal) in enumerate(
            zip(calculation_bars, diffs, deas, histograms, signal_types, strict=True)
        )
    )
    recent_signal = _recent_signal(points, trading_days)
    latest = points[-1]
    recent_signal_days = _recent_signal_days(
        recent_signal, trading_days, latest.bar_time.date()
    )
    signal_label = _signal_label(recent_signal, recent_signal_days, market_open)
    divergences = (
        calculate_macd_divergences(calculation_bars, points, trading_days)
        if period == "1d"
        else ()
    )
    return MacdCalculation(
        summary=MacdSummary(
            market=latest.market,
            code=latest.code,
            period=period,
            calculated_at=now,
            market_time=latest.bar_time,
            diff=latest.diff,
            dea=latest.dea,
            histogram=latest.histogram,
            signal_type=(
                MacdSignal.NONE if recent_signal is None else recent_signal.signal_type
            ),
            signal_date=None if recent_signal is None else recent_signal.bar_time.date(),
            recent_signal_days=recent_signal_days,
            recent_signal_label=signal_label,
            zero_axis=latest.zero_axis,
            status=_status(latest),
            is_intraday=latest.is_intraday,
            quality=quality,
        ),
        points=points,
        divergences=divergences,
    )


def _period_bars(bars: Sequence[Bar], period: str) -> list[Bar]:
    return sorted(
        (bar for bar in bars if bar.period == period),
        key=lambda bar: bar.bar_time,
    )


def _identity(bars: Sequence[Bar], latest_quote: Any | None) -> tuple[Market, str]:
    if bars:
        return bars[-1].market, bars[-1].code
    if latest_quote is not None:
        return latest_quote.market, latest_quote.code
    return Market.SH, ""


def _with_intraday_quote(
    bars: Sequence[Bar],
    latest_quote: Any | None,
    now: datetime,
    market_open: bool,
) -> tuple[list[Bar], bool]:
    result = list(bars)
    if (
        not market_open
        or latest_quote is None
        or latest_quote.latest_price is None
        or latest_quote.captured_at is None
        or latest_quote.captured_at.astimezone(SHANGHAI).date() != now.date()
    ):
        return result, False
    open_price = latest_quote.open_price or latest_quote.latest_price
    high_price = latest_quote.high_price or max(open_price, latest_quote.latest_price)
    low_price = latest_quote.low_price or min(open_price, latest_quote.latest_price)
    provisional = Bar(
        code=latest_quote.code,
        market=latest_quote.market,
        period="1d",
        adjustment="qfq",
        bar_time=datetime.combine(now.date(), time(15, 0), tzinfo=SHANGHAI),
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=latest_quote.latest_price,
        volume=latest_quote.volume or 0,
        amount=latest_quote.amount or 0.0,
        source=latest_quote.source or "latest-quote",
        is_complete=False,
        acquired_at=latest_quote.captured_at,
        quality_status=QualityStatus.PARTIAL,
    )
    if result and result[-1].bar_time.date() == provisional.bar_time.date():
        result[-1] = provisional
    else:
        result.append(provisional)
    return result, True


def _ema(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    ema_values = [float(values[0])]
    for value in values[1:]:
        ema_values.append(float(value) * multiplier + ema_values[-1] * (1 - multiplier))
    return ema_values


def _signals(diffs: Sequence[float], deas: Sequence[float]) -> list[MacdSignal]:
    signals = [MacdSignal.NONE]
    for index in range(1, len(diffs)):
        previous_diff = diffs[index - 1]
        previous_dea = deas[index - 1]
        current_diff = diffs[index]
        current_dea = deas[index]
        if previous_diff <= previous_dea and current_diff > current_dea:
            signals.append(MacdSignal.GOLDEN_CROSS)
        elif previous_diff >= previous_dea and current_diff < current_dea:
            signals.append(MacdSignal.DEATH_CROSS)
        else:
            signals.append(MacdSignal.NONE)
    return signals


def _zero_axis(diff: float | None) -> ZeroAxisPosition:
    if diff is None:
        return ZeroAxisPosition.UNKNOWN
    return ZeroAxisPosition.ABOVE if diff >= 0 else ZeroAxisPosition.BELOW


def _recent_signal(points: Sequence[MacdPoint], trading_days: Sequence[date]) -> MacdPoint | None:
    latest_date = points[-1].bar_time.date()
    for point in reversed(points):
        if point.signal_type is MacdSignal.NONE:
            continue
        days = _recent_signal_days(point, trading_days, latest_date)
        if days is not None and days <= 4:
            return point
        return None
    return None


def _recent_signal_days(
    point: MacdPoint | None,
    trading_days: Sequence[date],
    latest_date: date,
) -> int | None:
    if point is None:
        return None
    ordered_days = sorted({*trading_days, latest_date, point.bar_time.date()})
    positions = {trading_day: index for index, trading_day in enumerate(ordered_days)}
    return positions[latest_date] - positions[point.bar_time.date()]


def _signal_label(
    point: MacdPoint | None, recent_signal_days: int | None, market_open: bool
) -> str:
    if point is None:
        return "暂无"
    signal_name = "金叉" if point.signal_type is MacdSignal.GOLDEN_CROSS else "死叉"
    if recent_signal_days == 0:
        return f"盘中{signal_name}" if point.is_intraday and market_open else f"今日{signal_name}"
    if recent_signal_days is not None and recent_signal_days <= 2:
        return f"近 3 日{signal_name}"
    return f"近 5 日{signal_name}"


def _status(point: MacdPoint) -> str:
    if point.signal_type is MacdSignal.GOLDEN_CROSS:
        return "golden_after"
    if point.signal_type is MacdSignal.DEATH_CROSS:
        return "death_after"
    if point.diff is not None and point.dea is not None and point.diff > point.dea:
        return "bullish"
    if point.diff is not None and point.dea is not None and point.diff < point.dea:
        return "bearish"
    return "neutral"
