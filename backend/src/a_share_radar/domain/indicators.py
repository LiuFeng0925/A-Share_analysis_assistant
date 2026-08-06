from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from a_share_radar.domain.models import Market


class MacdSignal(StrEnum):
    NONE = "none"
    GOLDEN_CROSS = "golden_cross"
    DEATH_CROSS = "death_cross"


class ZeroAxisPosition(StrEnum):
    ABOVE = "above"
    BELOW = "below"
    UNKNOWN = "unknown"


class MacdQuality(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MacdPoint:
    market: Market
    code: str
    period: str
    bar_time: datetime
    diff: float | None
    dea: float | None
    histogram: float | None
    signal_type: MacdSignal
    zero_axis: ZeroAxisPosition
    is_intraday: bool
    quality: MacdQuality


@dataclass(frozen=True, slots=True)
class MacdSummary:
    market: Market
    code: str
    period: str
    calculated_at: datetime
    market_time: datetime | None
    diff: float | None
    dea: float | None
    histogram: float | None
    signal_type: MacdSignal
    signal_date: date | None
    recent_signal_days: int | None
    recent_signal_label: str
    zero_axis: ZeroAxisPosition
    status: str
    is_intraday: bool
    quality: MacdQuality


@dataclass(frozen=True, slots=True)
class MacdCalculation:
    summary: MacdSummary
    points: tuple[MacdPoint, ...]
