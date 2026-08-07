from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from a_share_radar.domain.indicators import (
    MacdDivergenceDirection,
    MacdDivergenceEvent,
    MacdDivergenceStatus,
    MacdPoint,
    MacdQuality,
    MacdSignal,
)
from a_share_radar.domain.models import Bar

PIVOT_SIDE_BARS = 3


@dataclass(slots=True)
class _Candidate:
    anchor_one_index: int
    anchor_two_index: int
    pivot_index: int
    detected_at: datetime
    updated_at: datetime
    confirmation_blocked: bool = False
    corresponding_signal: MacdSignal = MacdSignal.NONE
    corresponding_signal_time: datetime | None = None


def calculate_macd_divergences(
    bars: Sequence[Bar],
    points: Sequence[MacdPoint],
    trading_days: Sequence[date],
    *,
    evaluation_day: date | None = None,
    calculated_at: datetime | None = None,
    quality: MacdQuality | None = None,
) -> tuple[MacdDivergenceEvent, ...]:
    if len(bars) != len(points):
        raise ValueError("K 线与 MACD 点数量必须一致")
    if any(bar.bar_time != point.bar_time for bar, point in zip(bars, points, strict=True)):
        raise ValueError("K 线与 MACD 点时间必须一一对应")
    if not bars or bars[0].period != "1d":
        return ()
    lows = _confirmed_pivots(bars, points, MacdDivergenceDirection.BOTTOM)
    highs = _confirmed_pivots(bars, points, MacdDivergenceDirection.TOP)
    events = [
        *_scan_direction(
            bars,
            points,
            trading_days,
            lows,
            MacdDivergenceDirection.BOTTOM,
            evaluation_day or bars[-1].bar_time.date(),
            calculated_at or bars[-1].bar_time,
            quality or points[-1].quality,
        ),
        *_scan_direction(
            bars,
            points,
            trading_days,
            highs,
            MacdDivergenceDirection.TOP,
            evaluation_day or bars[-1].bar_time.date(),
            calculated_at or bars[-1].bar_time,
            quality or points[-1].quality,
        ),
    ]
    return tuple(sorted(events, key=lambda event: (event.detected_at, event.direction.value)))


def _confirmed_pivots(
    bars: Sequence[Bar],
    points: Sequence[MacdPoint],
    direction: MacdDivergenceDirection,
) -> tuple[int, ...]:
    pivots: list[int] = []
    for pivot_index in range(PIVOT_SIDE_BARS, len(bars) - PIVOT_SIDE_BARS):
        if points[pivot_index].diff is None:
            continue
        pivot_price = _price(bars[pivot_index], direction)
        surrounding_prices = (
            _price(bars[index], direction)
            for index in range(
                pivot_index - PIVOT_SIDE_BARS,
                pivot_index + PIVOT_SIDE_BARS + 1,
            )
            if index != pivot_index
        )
        if direction is MacdDivergenceDirection.BOTTOM and all(
            pivot_price < price for price in surrounding_prices
        ):
            pivots.append(pivot_index)
        if direction is MacdDivergenceDirection.TOP and all(
            pivot_price > price for price in surrounding_prices
        ):
            pivots.append(pivot_index)
    return tuple(pivots)


def _scan_direction(
    bars: Sequence[Bar],
    points: Sequence[MacdPoint],
    trading_days: Sequence[date],
    pivots: Sequence[int],
    direction: MacdDivergenceDirection,
    evaluation_day: date,
    calculated_at: datetime,
    quality: MacdQuality,
) -> tuple[MacdDivergenceEvent, ...]:
    events: list[MacdDivergenceEvent] = []
    candidate: _Candidate | None = None
    for index, point in enumerate(points):
        anchors = [pivot for pivot in pivots if pivot + PIVOT_SIDE_BARS <= index]
        if candidate is None:
            if len(anchors) < 2 or not _is_divergent(bars, points, anchors[-2], anchors[-1], index, direction):
                continue
            candidate = _Candidate(
                anchors[-2],
                anchors[-1],
                index,
                detected_at=point.bar_time,
                updated_at=point.bar_time,
            )
            _record_signal(candidate, point, direction)
            continue

        pivot_price = _price(bars[candidate.pivot_index], direction)
        current_price = _price(bars[index], direction)
        if _is_more_extreme(current_price, pivot_price, direction):
            if _is_divergent(
                bars,
                points,
                candidate.anchor_one_index,
                candidate.anchor_two_index,
                index,
                direction,
            ):
                candidate = _Candidate(
                    candidate.anchor_one_index,
                    candidate.anchor_two_index,
                    index,
                    detected_at=candidate.detected_at,
                    updated_at=point.bar_time,
                )
                _record_signal(candidate, point, direction)
                continue
            events.append(
                _event(
                    bars,
                    points,
                    trading_days,
                    candidate,
                    direction,
                    evaluation_day,
                    calculated_at,
                    quality,
                    invalidated_at=point.bar_time,
                )
            )
            candidate = None
            continue

        _record_signal(candidate, point, direction)
        if current_price == pivot_price:
            candidate.confirmation_blocked = True
        if (
            not candidate.confirmation_blocked
            and index - candidate.pivot_index >= PIVOT_SIDE_BARS
        ):
            events.append(
                _event(
                    bars,
                    points,
                    trading_days,
                    candidate,
                    direction,
                    evaluation_day,
                    calculated_at,
                    quality,
                    confirmed_at=point.bar_time,
                )
            )
            candidate = None

    if candidate is not None:
        events.append(
            _event(
                bars,
                points,
                trading_days,
                candidate,
                direction,
                evaluation_day,
                calculated_at,
                quality,
            )
        )
    return tuple(events)


def _price(bar: Bar, direction: MacdDivergenceDirection) -> float:
    return bar.low_price if direction is MacdDivergenceDirection.BOTTOM else bar.high_price


def _is_divergent(
    bars: Sequence[Bar],
    points: Sequence[MacdPoint],
    anchor_one_index: int,
    anchor_two_index: int,
    candidate_index: int,
    direction: MacdDivergenceDirection,
) -> bool:
    candidate_diff = points[candidate_index].diff
    anchor_one_diff = points[anchor_one_index].diff
    anchor_two_diff = points[anchor_two_index].diff
    if candidate_diff is None or anchor_one_diff is None or anchor_two_diff is None:
        return False
    candidate_price = _price(bars[candidate_index], direction)
    anchor_one_price = _price(bars[anchor_one_index], direction)
    anchor_two_price = _price(bars[anchor_two_index], direction)
    if direction is MacdDivergenceDirection.BOTTOM:
        return (
            candidate_price < anchor_one_price
            and candidate_price < anchor_two_price
            and candidate_diff > anchor_one_diff
            and candidate_diff > anchor_two_diff
        )
    return (
        candidate_price > anchor_one_price
        and candidate_price > anchor_two_price
        and candidate_diff < anchor_one_diff
        and candidate_diff < anchor_two_diff
    )


def _is_more_extreme(
    current_price: float, pivot_price: float, direction: MacdDivergenceDirection
) -> bool:
    if direction is MacdDivergenceDirection.BOTTOM:
        return current_price < pivot_price
    return current_price > pivot_price


def _record_signal(
    candidate: _Candidate,
    point: MacdPoint,
    direction: MacdDivergenceDirection,
) -> None:
    expected = (
        MacdSignal.GOLDEN_CROSS
        if direction is MacdDivergenceDirection.BOTTOM
        else MacdSignal.DEATH_CROSS
    )
    if candidate.corresponding_signal is MacdSignal.NONE and point.signal_type is expected:
        candidate.corresponding_signal = point.signal_type
        candidate.corresponding_signal_time = point.bar_time


def _event(
    bars: Sequence[Bar],
    points: Sequence[MacdPoint],
    trading_days: Sequence[date],
    candidate: _Candidate,
    direction: MacdDivergenceDirection,
    evaluation_day: date,
    calculated_at: datetime,
    quality: MacdQuality,
    *,
    confirmed_at: datetime | None = None,
    invalidated_at: datetime | None = None,
) -> MacdDivergenceEvent:
    anchor_one = candidate.anchor_one_index
    anchor_two = candidate.anchor_two_index
    pivot = candidate.pivot_index
    pivot_diff = points[pivot].diff
    anchor_one_diff = points[anchor_one].diff
    anchor_two_diff = points[anchor_two].diff
    assert pivot_diff is not None
    assert anchor_one_diff is not None
    assert anchor_two_diff is not None
    reference_time = confirmed_at or bars[pivot].bar_time
    return MacdDivergenceEvent(
        market=bars[pivot].market,
        code=bars[pivot].code,
        period=bars[pivot].period,
        direction=direction,
        status=(
            MacdDivergenceStatus.CONFIRMED
            if confirmed_at is not None
            else MacdDivergenceStatus.FORMING
        ),
        anchor_one_time=bars[anchor_one].bar_time,
        anchor_one_price=_price(bars[anchor_one], direction),
        anchor_one_diff=anchor_one_diff,
        anchor_two_time=bars[anchor_two].bar_time,
        anchor_two_price=_price(bars[anchor_two], direction),
        anchor_two_diff=anchor_two_diff,
        pivot_time=bars[pivot].bar_time,
        pivot_price=_price(bars[pivot], direction),
        pivot_diff=pivot_diff,
        detected_at=candidate.detected_at,
        updated_at=candidate.updated_at,
        calculated_at=calculated_at,
        quality=quality,
        confirmed_at=confirmed_at,
        invalidated_at=invalidated_at,
        is_valid=invalidated_at is None,
        corresponding_signal=candidate.corresponding_signal,
        corresponding_signal_time=candidate.corresponding_signal_time,
        recent_days=_recent_days(reference_time.date(), evaluation_day, trading_days),
    )


def _recent_days(reference_day: date, latest_day: date, trading_days: Sequence[date]) -> int:
    ordered_days = sorted({*trading_days, reference_day, latest_day})
    positions = {trading_day: index for index, trading_day in enumerate(ordered_days)}
    return max(0, positions[latest_day] - positions[reference_day])
