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


class KdjSignal(StrEnum):
    NONE = "none"
    GOLDEN_CROSS = "golden_cross"
    DEATH_CROSS = "death_cross"


class KdjZone(StrEnum):
    OVERSOLD = "oversold"
    NEUTRAL = "neutral"
    OVERBOUGHT = "overbought"
    UNKNOWN = "unknown"


class KdjSignalZone(StrEnum):
    LOW = "low"
    MIDDLE = "middle"
    HIGH = "high"
    UNKNOWN = "unknown"


class KdjQuality(StrEnum):
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


@dataclass(frozen=True, slots=True)
class KdjPoint:
    market: Market
    code: str
    period: str
    bar_time: datetime
    k_value: float | None
    d_value: float | None
    j_value: float | None
    signal_type: KdjSignal
    signal_zone: KdjSignalZone
    current_zone: KdjZone
    is_intraday: bool
    quality: KdjQuality


@dataclass(frozen=True, slots=True)
class KdjSummary:
    market: Market
    code: str
    period: str
    calculated_at: datetime
    market_time: datetime | None
    k_value: float | None
    d_value: float | None
    j_value: float | None
    current_zone: KdjZone
    signal_type: KdjSignal
    signal_time: datetime | None
    signal_zone: KdjSignalZone
    recent_signal_days: int | None
    recent_signal_label: str
    status: str
    is_intraday: bool
    quality: KdjQuality


@dataclass(frozen=True, slots=True)
class KdjCalculation:
    summary: KdjSummary
    points: tuple[KdjPoint, ...]
