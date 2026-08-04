from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class Market(StrEnum):
    SH = "SH"
    SZ = "SZ"
    BJ = "BJ"


class QualityStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    STALE = "stale"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Stock:
    code: str
    market: Market
    name: str
    list_status: str = "L"
    list_date: date | None = None


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    code: str
    market: Market
    name: str
    captured_at: datetime
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
    source: str
    quality_status: QualityStatus


@dataclass(frozen=True, slots=True)
class Bar:
    code: str
    market: Market
    period: str
    adjustment: str
    bar_time: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    amount: float
    source: str
    is_complete: bool = True
    acquired_at: datetime | None = None
    quality_status: QualityStatus | None = None

    def __post_init__(self) -> None:
        if self.acquired_at is None:
            object.__setattr__(self, "acquired_at", self.bar_time)
        if self.quality_status is None:
            object.__setattr__(
                self,
                "quality_status",
                QualityStatus.OK if self.is_complete else QualityStatus.PARTIAL,
            )


@dataclass(frozen=True, slots=True)
class BarFetchBatch:
    bars: tuple[Bar, ...]
    acquired_at: datetime
    source: str
    quality_status: QualityStatus
    raw_row_count: int
    invalid_row_count: int

    @property
    def valid_row_count(self) -> int:
        return len(self.bars)
