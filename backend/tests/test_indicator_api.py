from datetime import date, datetime
from zoneinfo import ZoneInfo

from httpx import ASGITransport, AsyncClient

from a_share_radar.domain.indicators import (
    MacdCalculation,
    MacdPoint,
    MacdQuality,
    MacdSignal,
    MacdSummary,
    ZeroAxisPosition,
)
from a_share_radar.domain.models import Market

TZ = ZoneInfo("Asia/Shanghai")


async def get(app, path, *, params=None):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, params=params)


def seed_macd(app, code: str = "600519", period: str = "1d") -> None:
    market_time = datetime(2026, 8, 4, 15, 0, tzinfo=TZ)
    app.state.repository.upsert_macd(
        MacdCalculation(
            summary=MacdSummary(
                market=Market.SH,
                code=code,
                period=period,
                calculated_at=datetime(2026, 8, 4, 15, 5, tzinfo=TZ),
                market_time=market_time,
                diff=0.18,
                dea=0.11,
                histogram=0.14,
                signal_type=MacdSignal.GOLDEN_CROSS,
                signal_date=date(2026, 8, 2),
                recent_signal_days=2,
                recent_signal_label="近 3 日金叉",
                zero_axis=ZeroAxisPosition.ABOVE,
                status="golden_after",
                is_intraday=False,
                quality=MacdQuality.OK,
            ),
            points=(
                MacdPoint(
                    market=Market.SH,
                    code=code,
                    period=period,
                    bar_time=market_time,
                    diff=0.18,
                    dea=0.11,
                    histogram=0.14,
                    signal_type=MacdSignal.GOLDEN_CROSS,
                    zero_axis=ZeroAxisPosition.ABOVE,
                    is_intraday=False,
                    quality=MacdQuality.OK,
                ),
            ),
        )
    )


async def test_stock_list_accepts_macd_filters_and_returns_signal_fields(app_with_fixture_data):
    seed_macd(app_with_fixture_data)

    response = await get(
        app_with_fixture_data,
        "/api/market/stocks",
        params={
            "macd_signal": "golden_cross",
            "macd_zero_axis": "above",
            "macd_recent_window": "3d",
            "page_size": 10,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["code"] == "600519"
    assert body["items"][0]["macd_signal_type"] == "golden_cross"
    assert body["items"][0]["macd_signal_label"] == "近 3 日金叉"
    assert body["items"][0]["macd_zero_axis"] == "above"
    assert body["items"][0]["macd_quality"] == "ok"


async def test_stock_macd_indicator_endpoint_returns_summary_and_series(app_with_fixture_data):
    seed_macd(app_with_fixture_data)

    response = await get(
        app_with_fixture_data,
        "/api/stocks/SH/600519/indicators/macd",
        params={"period": "1d"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["market"] == "SH"
    assert body["code"] == "600519"
    assert body["period"] == "1d"
    assert body["summary"]["recent_signal_label"] == "近 3 日金叉"
    assert body["summary"]["zero_axis"] == "above"
    assert body["items"][0]["histogram"] == 0.14


async def test_stock_macd_indicator_endpoint_refreshes_when_cache_is_missing(
    app_with_fixture_data,
):
    class FakeIndicatorService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool, str]] = []

        async def refresh_stock_macd(
            self,
            stock,
            now: datetime,
            *,
            market_open: bool,
            period: str = "1d",
        ) -> None:
            self.calls.append((stock.code, market_open, period))
            seed_macd(app_with_fixture_data, period=period)

    indicator_service = FakeIndicatorService()
    app_with_fixture_data.state.indicator_service = indicator_service
    app_with_fixture_data.state.clock = type(
        "ClosedClock",
        (),
        {"is_open": lambda self, at: False},
    )()

    response = await get(
        app_with_fixture_data,
        "/api/stocks/SH/600519/indicators/macd",
    )

    assert response.status_code == 200
    assert response.json()["summary"]["recent_signal_label"] == "近 3 日金叉"
    assert indicator_service.calls == [("600519", False, "1d")]


async def test_stock_macd_indicator_endpoint_accepts_minute_period_and_refreshes_that_period(
    app_with_fixture_data,
):
    class FakeIndicatorService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool, str]] = []

        async def refresh_stock_macd(
            self,
            stock,
            now: datetime,
            *,
            market_open: bool,
            period: str = "1d",
        ) -> None:
            self.calls.append((stock.code, market_open, period))
            seed_macd(app_with_fixture_data, period=period)

    indicator_service = FakeIndicatorService()
    app_with_fixture_data.state.indicator_service = indicator_service
    app_with_fixture_data.state.clock = type(
        "OpenClock",
        (),
        {"is_open": lambda self, at: True},
    )()

    response = await get(
        app_with_fixture_data,
        "/api/stocks/SH/600519/indicators/macd",
        params={"period": "5m"},
    )

    assert response.status_code == 200
    assert response.json()["period"] == "5m"
    assert indicator_service.calls == [("600519", True, "5m")]


async def test_stock_macd_indicator_returns_404_for_unknown_stock(app_with_fixture_data):
    response = await get(app_with_fixture_data, "/api/stocks/SH/999999/indicators/macd")

    assert response.status_code == 404
    assert response.json() == {"detail": "未找到该股票"}
