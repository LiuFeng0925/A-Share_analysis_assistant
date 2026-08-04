from dataclasses import replace
from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request

from a_share_radar.api.schemas import (
    BarResponse,
    BarSeriesResponse,
    DataStatusResponse,
    MarketSummaryResponse,
    StockPageResponse,
    StockQuoteResponse,
)
from a_share_radar.domain.models import Market
from a_share_radar.services.bar_service import BarQueryValidationError

router = APIRouter(prefix="/api")
SHANGHAI = ZoneInfo("Asia/Shanghai")
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


@router.get("/market/summary", response_model=MarketSummaryResponse)
def market_summary(request: Request):
    now = datetime.now(SHANGHAI)
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
):
    return request.app.state.repository.list_stocks(
        query, market, sort_by, sort_order, page, page_size
    )


@router.get("/stocks/{market}/{code}", response_model=StockQuoteResponse)
def stock_detail(request: Request, market: Market, code: str):
    stock = request.app.state.repository.get_stock(market, code)
    if stock is None:
        raise HTTPException(status_code=404, detail="未找到该股票")
    return stock


@router.get("/stocks/{market}/{code}/bars", response_model=BarSeriesResponse)
async def stock_bars(
    request: Request,
    market: Market,
    code: str,
    period: Period,
    range_name: Annotated[Range, Query(alias="range")],
    adjustment: Adjustment | None = None,
):
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
            datetime.now(SHANGHAI),
        )
    except BarQueryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return BarSeriesResponse(
        market=market,
        code=code,
        period=period,
        range=range_name,
        adjustment=resolved_adjustment,
        source=bars[-1].source if bars else None,
        last_updated_at=bars[-1].bar_time if bars else None,
        items=[BarResponse.model_validate(bar) for bar in bars],
    )


@router.get("/system/data-status", response_model=DataStatusResponse)
def data_status(request: Request):
    return request.app.state.repository.data_status()
