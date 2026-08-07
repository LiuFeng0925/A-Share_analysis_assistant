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


def weekend_trading_days() -> list[date]:
    return [
        date(2026, 7, 8),
        date(2026, 7, 9),
        date(2026, 7, 10),
        date(2026, 7, 13),
        date(2026, 7, 14),
        date(2026, 7, 15),
        date(2026, 7, 16),
        date(2026, 7, 17),
        date(2026, 7, 20),
        date(2026, 7, 21),
        date(2026, 7, 22),
        date(2026, 7, 23),
        date(2026, 7, 24),
        date(2026, 7, 27),
        date(2026, 7, 28),
        date(2026, 7, 29),
        date(2026, 7, 30),
        date(2026, 7, 31),
    ]


def _bars(
    prices: list[float], *, period: str = "1d", days: list[date] | None = None
) -> list[Bar]:
    days = trading_days() if days is None else days
    return [
        Bar(
            code="600519",
            market=Market.SH,
            period=period,
            adjustment="qfq",
            bar_time=datetime.combine(days[index], time(15, 0), tzinfo=TZ),
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
    bars: list[Bar],
    diffs: list[float | None],
    signals: dict[int, MacdSignal] | None = None,
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
            zero_axis=(
                ZeroAxisPosition.UNKNOWN
                if diff is None
                else ZeroAxisPosition.BELOW if diff < 0 else ZeroAxisPosition.ABOVE
            ),
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


def test_候选低点移动后形成时间跟随当前有效低点():
    bars = moved_candidate_bars()

    event = next(
        event for event in calculate_macd_divergences(
            bars,
            moved_candidate_points(),
            trading_days(),
        )
        if event.is_valid
    )

    assert event.detected_at == bars[-1].bar_time
    assert event.updated_at == bars[-1].bar_time


def test_背离事件记录本轮计算时间和MACD质量():
    calculated_at = datetime(2026, 7, 20, 16, 0, tzinfo=TZ)

    event = next(
        event for event in calculate_macd_divergences(
            bottom_forming_bars(),
            bottom_forming_points(),
            trading_days(),
            calculated_at=calculated_at,
            quality=MacdQuality.PARTIAL,
        )
        if event.is_valid
    )

    assert event.calculated_at == calculated_at
    assert event.quality is MacdQuality.PARTIAL


def test_价格和diff同步创新低会使形成中背离失效():
    event = calculate_macd_divergences(
        invalidated_bottom_bars(), invalidated_bottom_points(), trading_days()
    )[-1]

    assert event.is_valid is False
    assert event.invalidated_at == invalidated_bottom_bars()[-1].bar_time


def test_同步创新低后较高低点不能再作为底背离新低():
    bars = _bars([12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 7, 7.5, 8, 8.2])
    points = _points(
        bars,
        [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -5, -2, -1.8, -1.6],
    )

    events = calculate_macd_divergences(bars, points, trading_days())

    assert events == ()


def test_形成中右侧三根DIFF缺失仍会确认():
    bars = _bars([12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8, 8.5, 9, 10])
    points = _points(
        bars,
        [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2, None, None, None],
    )

    event = next(
        event for event in calculate_macd_divergences(bars, points, trading_days()) if event.is_valid
    )

    assert event.status is MacdDivergenceStatus.CONFIRMED
    assert event.confirmed_at == bars[-1].bar_time


def test_形成中DIFF缺失时价格创新低仍会失效():
    bars = _bars([12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8, 7.5])
    points = _points(
        bars, [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2, None]
    )

    event = calculate_macd_divergences(bars, points, trading_days())[-1]

    assert event.is_valid is False
    assert event.invalidated_at == bars[-1].bar_time


def test_跨周末形成中背离按交易日计算recent_days():
    days = weekend_trading_days()
    bars = _bars(
        [12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8, 8.5, 9], days=days
    )
    points = _points(
        bars, [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2, -1.8, -1.5]
    )

    event = calculate_macd_divergences(bars, points, days)[-1]

    assert event.status is MacdDivergenceStatus.FORMING
    assert event.recent_days == 2


def test_跨周末已确认背离按确认时间计算recent_days():
    days = weekend_trading_days()
    bars = _bars(
        [12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8, 8.5, 9, 10, 10.5, 11],
        days=days,
    )
    points = _points(
        bars,
        [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2, -1.8, -1.5, -1, -0.8, -0.5],
    )

    event = next(
        event for event in calculate_macd_divergences(bars, points, days) if event.is_valid
    )

    assert event.status is MacdDivergenceStatus.CONFIRMED
    assert event.recent_days == 2


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


def test_并列低点不能作为底背离历史锚点():
    bars = _bars([12, 11, 10, 9, 9, 11, 10, 9, 8.5, 9, 10, 9, 8])
    points = _points(
        bars,
        [-1, -2, -3, -4, -3.8, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2],
    )

    events = calculate_macd_divergences(bars, points, trading_days())

    assert events == ()


def test_并列高点不能作为顶背离历史锚点():
    bars = _bars([8, 9, 10, 11, 11, 9, 10, 11, 11.5, 11, 10, 11, 12])
    points = _points(
        bars,
        [1, 2, 3, 4, 3.8, 2, 2.5, 2.8, 3, 2, 1, 1.5, 2],
    )

    events = calculate_macd_divergences(bars, points, trading_days())

    assert events == ()


def test_底背离候选右侧出现同价后不能因满三根而确认():
    bars = _bars([12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8, 8, 9, 10])
    points = _points(
        bars,
        [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2, -1.9, -1.5, -1],
    )

    event = next(
        event for event in calculate_macd_divergences(bars, points, trading_days())
        if event.is_valid
    )

    assert event.status is MacdDivergenceStatus.FORMING
    assert event.pivot_time == bars[12].bar_time


def test_顶背离候选右侧出现同价后不能因满三根而确认():
    bars = _bars([8, 9, 10, 11, 10, 9, 10, 11, 11.5, 11, 10, 11, 12, 12, 11, 10])
    points = _points(
        bars,
        [1, 2, 3, 4, 3, 2, 2.5, 2.8, 3, 2, 1, 1.5, 2, 1.9, 1.5, 1],
    )

    event = next(
        event for event in calculate_macd_divergences(bars, points, trading_days())
        if event.is_valid
    )

    assert event.status is MacdDivergenceStatus.FORMING
    assert event.pivot_time == bars[12].bar_time


def test_停牌后形成中背离年龄随交易日历推进但确认K数不推进():
    days = weekend_trading_days()
    bars = _bars(
        [12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8],
        days=days,
    )
    points = _points(
        bars,
        [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2],
    )

    event = next(
        event for event in calculate_macd_divergences(
            bars,
            points,
            days,
            evaluation_day=days[16],
        )
        if event.is_valid
    )

    assert event.status is MacdDivergenceStatus.FORMING
    assert event.recent_days == 4


def test_停牌后已确认背离年龄从确认日随交易日历推进():
    days = weekend_trading_days()
    bars = _bars(
        [12, 11, 10, 9, 10, 11, 10, 9, 8.5, 9, 10, 9, 8, 8.5, 9, 10],
        days=days,
    )
    points = _points(
        bars,
        [-1, -2, -3, -4, -3, -2, -2.5, -2.8, -3, -2, -1, -1.5, -2, -1.8, -1.5, -1],
    )

    event = next(
        event for event in calculate_macd_divergences(
            bars,
            points,
            days,
            evaluation_day=days[17],
        )
        if event.is_valid
    )

    assert event.status is MacdDivergenceStatus.CONFIRMED
    assert event.recent_days == 2


def test_显式评估日早于形成节点时recent_days防御性钳制为零():
    bars = bottom_forming_bars()

    event = next(
        event for event in calculate_macd_divergences(
            bars,
            bottom_forming_points(),
            trading_days(),
            evaluation_day=bars[-2].bar_time.date(),
        )
        if event.is_valid
    )

    assert event.status is MacdDivergenceStatus.FORMING
    assert event.recent_days == 0


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
