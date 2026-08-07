import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from a_share_radar.api.schemas import (
    BarResponse,
    BarSeriesResponse,
    DataStatusResponse,
    MacdIndicatorResponse,
    MarketSummaryResponse,
    StockPageResponse,
    StockQuoteResponse,
)
from a_share_radar.domain.models import Market, QualityStatus
from a_share_radar.services.bar_service import (
    BarQueryValidationError,
    BarStockNotFoundError,
)

router = APIRouter(prefix="/api")
SortField = Literal[
    "code",
    "latest_price",
    "change_percent",
    "amount",
    "turnover_rate",
    "total_market_cap",
]
SortOrder = Literal["asc", "desc"]
Period = Literal["1m", "5m", "15m", "30m", "60m", "1d", "1w", "1mo"]
Range = Literal["today", "5d", "60d", "6mo", "ytd", "1y", "5y", "all"]
Adjustment = Literal["none", "qfq", "hfq"]
MacdSignalQuery = Literal["golden_cross", "death_cross"]
MacdZeroAxisQuery = Literal["above", "below"]
MacdRecentWindowQuery = Literal["today", "3d", "5d"]
MacdDivergenceRecentWindowQuery = Literal["today", "3d", "5d", "10d", "20d"]
MacdDivergenceQuery = Literal[
    "bottom_forming",
    "bottom_confirmed",
    "top_forming",
    "top_confirmed",
]
MacdDivergenceCrossQuery = Literal["present", "absent"]
IndicatorPeriod = Literal["1m", "5m", "15m", "30m", "60m", "1d", "1w", "1mo"]


def request_now(request: Request) -> datetime:
    return request.app.state.now_provider()


@router.get("/market/summary", response_model=MarketSummaryResponse)
def market_summary(request: Request):
    now = request_now(request)
    summary = request.app.state.repository.market_summary(
        request.app.state.settings.stale_after_seconds, now
    )
    clock = getattr(request.app.state, "clock", None)
    if clock is not None:
        summary = replace(
            summary, market_status="open" if clock.is_open(now) else "closed"
        )
    return summary


@router.get("/market/stocks", response_model=StockPageResponse)
def stock_list(
    request: Request,
    query: str | None = None,
    market: Market | None = None,
    sort_by: SortField = "code",
    sort_order: SortOrder = "asc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=10, le=200)] = 50,
    macd_signal: MacdSignalQuery | None = None,
    macd_zero_axis: MacdZeroAxisQuery | None = None,
    macd_recent_window: MacdRecentWindowQuery | None = None,
    macd_divergences: Annotated[list[MacdDivergenceQuery] | None, Query()] = None,
    macd_divergence_cross: MacdDivergenceCrossQuery | None = None,
    macd_divergence_recent_window: MacdDivergenceRecentWindowQuery | None = None,
):
    return request.app.state.repository.list_stocks(
        query,
        market,
        sort_by,
        sort_order,
        page,
        page_size,
        macd_signal=macd_signal,
        macd_zero_axis=macd_zero_axis,
        macd_recent_window=macd_recent_window,
        macd_divergences=macd_divergences,
        macd_divergence_cross=macd_divergence_cross,
        macd_divergence_recent_window=macd_divergence_recent_window,
    )


@router.get("/stocks/{market}/{code}", response_model=StockQuoteResponse)
def stock_detail(request: Request, market: Market, code: str):
    stock = request.app.state.repository.get_stock(market, code)
    if stock is None:
        raise HTTPException(status_code=404, detail="未找到该股票")
    return stock


@router.get(
    "/stocks/{market}/{code}/indicators/macd",
    response_model=MacdIndicatorResponse,
)
async def stock_macd_indicator(
    request: Request,
    market: Market,
    code: str,
    period: IndicatorPeriod = "1d",
):
    stock = await asyncio.to_thread(
        request.app.state.repository.get_stock, market, code
    )
    if stock is None:
        raise HTTPException(status_code=404, detail="未找到该股票")
    indicator_service = getattr(request.app.state, "indicator_service", None)
    at = request_now(request)
    clock = getattr(request.app.state, "clock", None)
    market_open = bool(clock is not None and clock.is_open(at))
    if indicator_service is not None and hasattr(indicator_service, "get_stock_macd"):
        calculation = await indicator_service.get_stock_macd(
            stock,
            at,
            market_open=market_open,
            period=period,
        )
    else:
        calculation = await asyncio.to_thread(
            request.app.state.repository.get_macd, market, code, period
        )
        if calculation is None and indicator_service is not None:
            await indicator_service.refresh_stock_macd(
                stock,
                at,
                market_open=market_open,
                period=period,
            )
            calculation = await asyncio.to_thread(
                request.app.state.repository.get_macd, market, code, period
            )
    if calculation is None:
        raise HTTPException(status_code=404, detail="MACD 指标暂不可用")
    return MacdIndicatorResponse(
        market=market,
        code=code,
        period=period,
        summary=calculation.summary,
        items=list(calculation.points),
        divergences=(
            [event for event in calculation.divergences if event.is_valid]
            if period == "1d"
            else []
        ),
    )


@router.get("/stocks/{market}/{code}/bars", response_model=BarSeriesResponse)
async def stock_bars(
    request: Request,
    market: Market,
    code: str,
    period: Period,
    range_name: Annotated[Range, Query(alias="range")],
    adjustment: Adjustment | None = None,
):
    stock = await asyncio.to_thread(
        request.app.state.repository.get_stock, market, code
    )
    if stock is None:
        raise HTTPException(status_code=404, detail="未找到该股票")
    if adjustment is None:
        resolved_adjustment: Adjustment = "none" if period == "1m" else "qfq"
    else:
        resolved_adjustment = adjustment
    try:
        bars = await request.app.state.bar_service.get_bars(
            market,
            code,
            period,
            range_name,
            resolved_adjustment,
            request_now(request),
        )
    except BarQueryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BarStockNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ingestion = await request.app.state.bar_service.latest_ingestion(
        market, code, period, resolved_adjustment
    )
    return BarSeriesResponse(
        market=market,
        code=code,
        period=period,
        range=range_name,
        adjustment=resolved_adjustment,
        source=bars[-1].source if bars else ingestion.source if ingestion else None,
        last_updated_at=bars[-1].acquired_at if bars else None,
        fetch_quality_status=(
            QualityStatus(ingestion.quality_status) if ingestion else None
        ),
        last_fetch_at=ingestion.acquired_at if ingestion else None,
        fetch_raw_row_count=ingestion.raw_row_count if ingestion else None,
        fetch_valid_row_count=ingestion.valid_row_count if ingestion else None,
        fetch_invalid_row_count=ingestion.invalid_row_count if ingestion else None,
        items=[BarResponse.model_validate(bar) for bar in bars],
    )


@router.get("/system/data-status", response_model=DataStatusResponse)
def data_status(request: Request):
    return request.app.state.repository.data_status()
