from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from a_share_radar.domain.indicators import (
    MacdDivergenceDirection,
    MacdDivergenceStatus,
    MacdPoint,
    MacdQuality,
    MacdSignal,
    ZeroAxisPosition,
)
from a_share_radar.domain.models import Bar, Market, QualityStatus
from a_share_radar.services.macd_divergence import calculate_macd_divergences

TZ = ZoneInfo("Asia/Shanghai")


def trading_days() -> list[date]:
    return [date(2026, 7, 1) + timedelta(days=index) for index in range(20)]


def _bars(prices: list[float], *, period: str = "1d") -> list[Bar]:
    return [
        Bar(
            code="600519",
            market=Market.SH,
            period=period,
            adjustment="qfq",
            bar_time=datetime.combine(trading_days()[index], time(15, 0), tzinfo=TZ),
            open_price=price,
            high_price=price,
            low_price=price,
            close_price=price,
            volume=1_000_000,
            amount=price * 1_000_000,
            source="fixture",
            is_complete=True,
            quality_status=QualityStatus.OK,
        )
        for index, price in enumerate(prices)
    ]


def _points(
    bars: list[Bar], diffs: list[float], signals: dict[int, MacdSignal] | None = None
) -> list[MacdPoint]:
    signals = signals or {}
    return [
        MacdPoint(
            market=bar.market,
            code=bar.code,
            period=bar.period,
            bar_time=bar.bar_time,
            diff=diff,
            dea=diff,
            histogram=0.0,
            signal_type=signals.get(index, MacdSignal.NONE),
            zero_axis=ZeroAxisPosition.BELOW if diff < 0 else ZeroAxisPosition.ABOVE,
            is_intraday=False,
            quality=MacdQuality.OK,
        )
        for index, (bar, diff) in enumerate(zip(bars, diffs, strict=True))
    ]


def bottom_forming_bars() -> list[Bar]:
    return _bars([12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8])


def bottom_forming_points() -> list[MacdPoint]:
    bars = bottom_forming_bars()
    return _points(bars, [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2])


def bottom_confirmed_bars() -> list[Bar]:
    return _bars([12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8, 8.5, 9, 10])


def bottom_confirmed_points() -> list[MacdPoint]:
    bars = bottom_confirmed_bars()
    return _points(
        bars, [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2, -1.8, -1.5, -1]
    )


def moved_candidate_bars() -> list[Bar]:
    return _bars([12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8, 7.5])


def moved_candidate_points() -> list[MacdPoint]:
    bars = moved_candidate_bars()
    return _points(
        bars,
        [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2, -1.8],
        {12: MacdSignal.GOLDEN_CROSS},
    )


def invalidated_bottom_bars() -> list[Bar]:
    return moved_candidate_bars()


def invalidated_bottom_points() -> list[MacdPoint]:
    bars = invalidated_bottom_bars()
    return _points(bars, [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2, -5])


def top_forming_bars() -> list[Bar]:
    return _bars([8, 9, 10, 11, 10, 9, 10, 11, 12, 11, 10, 11, 13])


def top_forming_points() -> list[MacdPoint]:
    bars = top_forming_bars()
    return _points(bars, [1, 2, 3, 4, 3, 2, 2.5, 2.8, 3, 2, 1, 1.5, 2])


def test_当前创新低立即形成底背离且不等待右侧三根():
    events = calculate_macd_divergences(
        bottom_forming_bars(), bottom_forming_points(), trading_days()
    )

    event = events[-1]
    assert event.direction is MacdDivergenceDirection.BOTTOM
    assert event.status is MacdDivergenceStatus.FORMING
    assert event.pivot_time == bottom_forming_bars()[-1].bar_time
    assert event.is_valid is True


def test_右侧三根均未创新低后升级为底背离已确认():
    events = calculate_macd_divergences(
        bottom_confirmed_bars(), bottom_confirmed_points(), trading_days()
    )

    event = next(event for event in events if event.is_valid)
    assert event.status is MacdDivergenceStatus.CONFIRMED
    assert event.confirmed_at == bottom_confirmed_bars()[-1].bar_time


def test_候选低点移动后旧金叉不再作为对应交叉():
    events = calculate_macd_divergences(
        moved_candidate_bars(), moved_candidate_points(), trading_days()
    )

    event = next(event for event in events if event.is_valid)
    assert event.pivot_time == moved_candidate_bars()[-1].bar_time
    assert event.corresponding_signal is MacdSignal.NONE
    assert event.corresponding_signal_time is None


def test_价格和diff同步创新低会使形成中背离失效():
    event = calculate_macd_divergences(
        invalidated_bottom_bars(), invalidated_bottom_points(), trading_days()
    )[-1]

    assert event.is_valid is False
    assert event.invalidated_at == invalidated_bottom_bars()[-1].bar_time


def test_形成中候选当日金叉会记录对应交叉():
    events = calculate_macd_divergences(
        bottom_forming_bars(),
        _points(bottom_forming_bars(), [
            -1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2
        ], {12: MacdSignal.GOLDEN_CROSS}),
        trading_days(),
    )

    event = events[-1]
    assert event.corresponding_signal is MacdSignal.GOLDEN_CROSS
    assert event.corresponding_signal_time == bottom_forming_bars()[-1].bar_time


def test_顶背离要求价格高于两个锚点且diff低于两个锚点():
    event = next(
        event
        for event in calculate_macd_divergences(
            top_forming_bars(), top_forming_points(), trading_days()
        )
        if event.is_valid
    )

    assert event.direction is MacdDivergenceDirection.TOP
    assert event.status is MacdDivergenceStatus.FORMING


def test_非日线不会计算背离():
    bars = _bars([12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8], period="5m")

    assert calculate_macd_divergences(bars, _points(bars, [-1] * len(bars)), trading_days()) == ()


def test_K线和MACD点数量不一致会报错():
    bars = bottom_forming_bars()

    with pytest.raises(ValueError, match="K 线与 MACD 点数量必须一致"):
        calculate_macd_divergences(bars, bottom_forming_points()[:-1], trading_days())


def test_K线和MACD点时间不一致会报错():
    bars = bottom_forming_bars()
    points = bottom_forming_points()
    points[-1] = MacdPoint(
        market=points[-1].market,
        code=points[-1].code,
        period=points[-1].period,
        bar_time=points[-2].bar_time,
        diff=points[-1].diff,
        dea=points[-1].dea,
        histogram=points[-1].histogram,
        signal_type=points[-1].signal_type,
        zero_axis=points[-1].zero_axis,
        is_intraday=points[-1].is_intraday,
        quality=points[-1].quality,
    )

    with pytest.raises(ValueError, match="K 线与 MACD 点时间必须一一对应"):
        calculate_macd_divergences(bars, points, trading_days())
