from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from a_share_radar.domain.models import Market, QualityStatus


class AttributeModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MarketSummaryResponse(AttributeModel):
    total: int
    rising: int
    falling: int
    flat: int
    amount: float
    market_status: Literal["open", "closed"]
    last_updated_at: AwareDatetime | None
    stale: bool


class StockQuoteResponse(AttributeModel):
    market: Market
    code: str
    name: str
    latest_price: float | None
    change_percent: float | None
    change_amount: float | None
    open_price: float | None
    high_price: float | None
    low_price: float | None
    previous_close: float | None
    volume: int | None
    amount: float | None
    turnover_rate: float | None
    total_market_cap: float | None
    captured_at: AwareDatetime | None
    quality_status: QualityStatus | None


class StockPageResponse(AttributeModel):
    items: list[StockQuoteResponse]
    total: int
    page: int
    page_size: int


class BarResponse(AttributeModel):
    bar_time: AwareDatetime
    acquired_at: AwareDatetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    amount: float
    is_complete: bool
    quality_status: QualityStatus


class BarSeriesResponse(AttributeModel):
    market: Market
    code: str
    period: str
    range: str
    adjustment: str
    source: str | None
    last_updated_at: AwareDatetime | None
    fetch_quality_status: QualityStatus | None
    last_fetch_at: AwareDatetime | None
    fetch_raw_row_count: int | None
    fetch_valid_row_count: int | None
    fetch_invalid_row_count: int | None
    items: list[BarResponse]


class DataStatusResponse(AttributeModel):
    stock_count: int
    latest_quote_count: int
    snapshot_count: int
    bar_count: int
    latest_captured_at: AwareDatetime | None
    latest_success_at: AwareDatetime | None
    latest_failure_at: AwareDatetime | None
    latest_market_time: AwareDatetime | None
    snapshot_expected_count: int | None
    snapshot_actual_count: int | None
    snapshot_coverage_ratio: float | None
    snapshot_quality_status: str | None
