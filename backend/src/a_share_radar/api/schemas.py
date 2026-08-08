from datetime import date
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
    macd_signal_type: str | None = None
    macd_signal_date: date | None = None
    macd_recent_signal_days: int | None = None
    macd_signal_label: str | None = None
    macd_zero_axis: str | None = None
    macd_quality: str | None = None
    kdj_signal_type: str | None = None
    kdj_signal_time: AwareDatetime | None = None
    kdj_recent_signal_days: int | None = None
    kdj_signal_label: str | None = None
    kdj_signal_zone: str | None = None
    kdj_current_zone: str | None = None
    kdj_quality: str | None = None


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


class MacdPointResponse(AttributeModel):
    bar_time: AwareDatetime
    diff: float | None
    dea: float | None
    histogram: float | None
    signal_type: str
    zero_axis: str
    is_intraday: bool
    quality: str


class MacdSummaryResponse(AttributeModel):
    calculated_at: AwareDatetime
    market_time: AwareDatetime | None
    diff: float | None
    dea: float | None
    histogram: float | None
    signal_type: str
    signal_date: date | None
    recent_signal_days: int | None
    recent_signal_label: str
    zero_axis: str
    status: str
    is_intraday: bool
    quality: str


class MacdIndicatorResponse(AttributeModel):
    market: Market
    code: str
    period: str
    summary: MacdSummaryResponse
    items: list[MacdPointResponse]


class KdjPointResponse(AttributeModel):
    bar_time: AwareDatetime
    k_value: float | None
    d_value: float | None
    j_value: float | None
    signal_type: str
    signal_zone: str
    current_zone: str
    is_intraday: bool
    quality: str


class KdjSummaryResponse(AttributeModel):
    calculated_at: AwareDatetime
    market_time: AwareDatetime | None
    k_value: float | None
    d_value: float | None
    j_value: float | None
    current_zone: str
    signal_type: str
    signal_time: AwareDatetime | None
    signal_zone: str
    recent_signal_days: int | None
    recent_signal_label: str
    status: str
    is_intraday: bool
    quality: str


class KdjIndicatorResponse(AttributeModel):
    market: Market
    code: str
    period: str
    summary: KdjSummaryResponse
    items: list[KdjPointResponse]


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
