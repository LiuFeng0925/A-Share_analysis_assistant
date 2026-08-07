from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pytest

from a_share_radar.domain.indicators import (
    MacdCalculation,
    MacdDivergenceDirection,
    MacdDivergenceEvent,
    MacdDivergenceStatus,
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
    divergences: tuple[MacdDivergenceEvent, ...] = (),
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
    return MacdCalculation(summary=summary, points=(point,), divergences=divergences)


def bottom_divergence(
    code: str, *, period: str = "1d", detected_at: datetime | None = None
) -> MacdDivergenceEvent:
    detected_at = detected_at or datetime(2026, 8, 6, 15, 0, tzinfo=TZ)
    return MacdDivergenceEvent(
        market=Market.SH,
        code=code,
        period=period,
        direction=MacdDivergenceDirection.BOTTOM,
        status=MacdDivergenceStatus.CONFIRMED,
        anchor_one_time=datetime(2026, 7, 28, 15, 0, tzinfo=TZ),
        anchor_one_price=100.0,
        anchor_one_diff=-1.2,
        anchor_two_time=datetime(2026, 8, 1, 15, 0, tzinfo=TZ),
        anchor_two_price=98.0,
        anchor_two_diff=-1.0,
        pivot_time=detected_at,
        pivot_price=95.0,
        pivot_diff=-0.8,
        detected_at=detected_at,
        confirmed_at=datetime(2026, 8, 7, 15, 0, tzinfo=TZ),
        invalidated_at=None,
        is_valid=True,
        corresponding_signal=MacdSignal.GOLDEN_CROSS,
        corresponding_signal_time=datetime(2026, 8, 7, 15, 0, tzinfo=TZ),
        recent_days=1,
    )


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


def test_仓储读取后逐字段保留macd背离事件(repository):
    event = bottom_divergence("600519")
    calculation = macd_calculation(
        "600519",
        signal=MacdSignal.GOLDEN_CROSS,
        zero_axis=ZeroAxisPosition.ABOVE,
        days=2,
        label="近 3 日金叉",
        divergences=(event,),
    )

    repository.upsert_macd(calculation)

    stored = repository.get_macd(Market.SH, "600519")
    assert stored is not None
    assert stored.divergences == (event,)


def test_同一股票周期再次保存会替换旧背离事件(repository):
    first = macd_calculation(
        "600519",
        signal=MacdSignal.GOLDEN_CROSS,
        zero_axis=ZeroAxisPosition.ABOVE,
        days=2,
        label="近 3 日金叉",
        divergences=(bottom_divergence("600519"),),
    )
    second = replace(first, divergences=())
    repository.upsert_macd(first)

    repository.upsert_macd(second)

    stored = repository.get_macd(Market.SH, "600519")
    assert stored is not None
    assert stored.divergences == ()


def test_替换背离事件不影响其他股票(repository):
    repository.upsert_macd(
        macd_calculation(
            "600519",
            signal=MacdSignal.GOLDEN_CROSS,
            zero_axis=ZeroAxisPosition.ABOVE,
            days=2,
            label="近 3 日金叉",
            divergences=(bottom_divergence("600519"),),
        )
    )
    other_event = bottom_divergence("601318")
    repository.upsert_macd(
        macd_calculation(
            "601318",
            signal=MacdSignal.GOLDEN_CROSS,
            zero_axis=ZeroAxisPosition.ABOVE,
            days=2,
            label="近 3 日金叉",
            divergences=(other_event,),
        )
    )

    repository.upsert_macd(
        macd_calculation(
            "600519",
            signal=MacdSignal.GOLDEN_CROSS,
            zero_axis=ZeroAxisPosition.ABOVE,
            days=2,
            label="近 3 日金叉",
            divergences=(),
        )
    )

    stored = repository.get_macd(Market.SH, "601318")
    assert stored is not None
    assert stored.divergences == (other_event,)


def test_保存背离事件失败会回滚摘要序列和旧事件(repository):
    first = macd_calculation(
        "600519",
        signal=MacdSignal.GOLDEN_CROSS,
        zero_axis=ZeroAxisPosition.ABOVE,
        days=2,
        label="近 3 日金叉",
        divergences=(bottom_divergence("600519"),),
    )
    repository.upsert_macd(first)
    invalid = replace(
        first,
        summary=replace(first.summary, diff=9.99),
        points=(replace(first.points[0], diff=9.99),),
        divergences=(bottom_divergence("600519"), bottom_divergence("600519")),
    )

    with pytest.raises(duckdb.ConstraintException):
        repository.upsert_macd(invalid)

    stored = repository.get_macd(Market.SH, "600519")
    assert stored == first


@pytest.mark.parametrize(
    ("field", "value"),
    [("market", Market.SZ), ("code", "000001"), ("period", "5m")],
    ids=["市场", "代码", "周期"],
)
def test_macd点标识不一致会在事务前拒绝写入并保留旧数据(repository, field, value):
    first = macd_calculation(
        "600519",
        signal=MacdSignal.GOLDEN_CROSS,
        zero_axis=ZeroAxisPosition.ABOVE,
        days=2,
        label="近 3 日金叉",
        divergences=(bottom_divergence("600519"),),
    )
    repository.upsert_macd(first)
    invalid = replace(first, points=(replace(first.points[0], **{field: value}),))

    with pytest.raises(ValueError, match="MACD 点与摘要标识不一致"):
        repository.upsert_macd(invalid)

    assert repository.get_macd(Market.SH, "600519") == first


@pytest.mark.parametrize(
    ("field", "value"),
    [("market", Market.SZ), ("code", "000001"), ("period", "5m")],
    ids=["市场", "代码", "周期"],
)
def test_macd背离事件标识不一致会在事务前拒绝写入并保留旧数据(
    repository, field, value
):
    first = macd_calculation(
        "600519",
        signal=MacdSignal.GOLDEN_CROSS,
        zero_axis=ZeroAxisPosition.ABOVE,
        days=2,
        label="近 3 日金叉",
        divergences=(bottom_divergence("600519"),),
    )
    repository.upsert_macd(first)
    invalid = replace(
        first,
        divergences=(replace(first.divergences[0], **{field: value}),),
    )

    with pytest.raises(ValueError, match="MACD 背离事件与摘要标识不一致"):
        repository.upsert_macd(invalid)

    assert repository.get_macd(Market.SH, "600519") == first


def test_替换同一股票日线背离不影响分钟周期(repository):
    daily = macd_calculation(
        "600519",
        signal=MacdSignal.GOLDEN_CROSS,
        zero_axis=ZeroAxisPosition.ABOVE,
        days=2,
        label="近 3 日金叉",
        divergences=(bottom_divergence("600519"),),
    )
    minute_event = bottom_divergence("600519", period="5m")
    minute = macd_calculation(
        "600519",
        signal=MacdSignal.GOLDEN_CROSS,
        zero_axis=ZeroAxisPosition.ABOVE,
        days=0,
        label="今日金叉",
        period="5m",
        divergences=(minute_event,),
    )
    repository.upsert_macd(daily)
    repository.upsert_macd(minute)

    repository.upsert_macd(replace(daily, divergences=()))

    stored = repository.get_macd(Market.SH, "600519", period="5m")
    assert stored is not None
    assert stored.divergences == (minute_event,)


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
