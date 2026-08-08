from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from a_share_radar.domain.indicators import (
    KdjCalculation,
    KdjPoint,
    KdjQuality,
    KdjSignal,
    KdjSignalZone,
    KdjSummary,
    KdjZone,
)
from a_share_radar.domain.models import Market, Stock

TZ = ZoneInfo("Asia/Shanghai")


def kdj_calculation(
    code: str,
    *,
    period: str = "1d",
    signal: KdjSignal = KdjSignal.GOLDEN_CROSS,
    signal_zone: KdjSignalZone = KdjSignalZone.LOW,
    days: int | None = 0,
) -> KdjCalculation:
    market_time = datetime(2026, 8, 6, 15, 0, tzinfo=TZ)
    point = KdjPoint(
        market=Market.SH,
        code=code,
        period=period,
        bar_time=market_time,
        k_value=18.2,
        d_value=17.1,
        j_value=20.4,
        signal_type=signal,
        signal_zone=signal_zone,
        current_zone=KdjZone.OVERSOLD,
        is_intraday=False,
        quality=KdjQuality.OK,
    )
    return KdjCalculation(
        summary=KdjSummary(
            market=Market.SH,
            code=code,
            period=period,
            calculated_at=datetime(2026, 8, 6, 15, 5, tzinfo=TZ),
            market_time=market_time,
            k_value=point.k_value,
            d_value=point.d_value,
            j_value=point.j_value,
            current_zone=point.current_zone,
            signal_type=signal,
            signal_time=market_time if signal is not KdjSignal.NONE else None,
            signal_zone=(signal_zone if signal is not KdjSignal.NONE else KdjSignalZone.UNKNOWN),
            recent_signal_days=days,
            recent_signal_label=(
                "今日金叉" if signal is KdjSignal.GOLDEN_CROSS else "今日死叉"
            ),
            status="golden_after" if signal is KdjSignal.GOLDEN_CROSS else "death_after",
            is_intraday=False,
            quality=KdjQuality.OK,
        ),
        points=(point,),
    )


def test_repository_saves_and_loads_kdj_calculation(repository):
    calculation = kdj_calculation("600519", period="30m")

    repository.upsert_kdj(calculation)

    stored = repository.get_kdj(Market.SH, "600519", "30m")
    assert stored == calculation


def test_repository_replaces_one_kdj_period_without_touching_another(repository):
    minute = kdj_calculation("600519", period="30m")
    daily = kdj_calculation("600519", period="1d")
    repository.upsert_kdj(minute)
    repository.upsert_kdj(daily)
    shorter = replace(minute, points=())

    repository.upsert_kdj(shorter)

    assert repository.get_kdj(Market.SH, "600519", "30m") == shorter
    assert repository.get_kdj(Market.SH, "600519", "1d") == daily


def test_stock_row_returns_daily_kdj_but_ignores_minute_kdj(repository):
    repository.upsert_stocks(
        [
            Stock("600519", Market.SH, "贵州茅台"),
            Stock("601318", Market.SH, "中国平安"),
        ]
    )
    repository.upsert_kdj(kdj_calculation("600519", period="1d"))
    repository.upsert_kdj(kdj_calculation("601318", period="30m"))

    page = repository.list_stocks(None, None, "code", "asc", 1, 50)

    assert page.items[0].kdj_signal_type is KdjSignal.GOLDEN_CROSS
    assert page.items[0].kdj_signal_zone is KdjSignalZone.LOW
    assert page.items[0].kdj_current_zone is KdjZone.OVERSOLD
    assert page.items[0].kdj_quality is KdjQuality.OK
    assert page.items[1].kdj_signal_type is None
