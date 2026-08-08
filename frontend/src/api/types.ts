export type Market = "SH" | "SZ" | "BJ";
export type QualityStatus = "ok" | "partial" | "stale" | "error";
export type BarPeriod = "1m" | "5m" | "15m" | "30m" | "60m" | "1d" | "1w" | "1mo";
export type BarRange = "today" | "5d" | "60d" | "6mo" | "ytd" | "1y" | "5y" | "all";
export type Adjustment = "none" | "qfq" | "hfq";
export type MacdSignal = "none" | "golden_cross" | "death_cross";
export type MacdSignalFilter = Exclude<MacdSignal, "none">;
export type MacdZeroAxis = "above" | "below" | "unknown";
export type MacdZeroAxisFilter = Exclude<MacdZeroAxis, "unknown">;
export type MacdRecentWindow = "today" | "3d" | "5d";
export type MacdDivergenceRecentWindow = MacdRecentWindow | "10d" | "20d";
export type MacdDivergenceFilter =
  | "bottom_forming"
  | "bottom_confirmed"
  | "top_forming"
  | "top_confirmed";
export type MacdDivergenceCrossFilter = "present" | "absent";
export type MacdQuality = "ok" | "partial" | "insufficient" | "error";
export type KdjSignal = "none" | "golden_cross" | "death_cross";
export type KdjSignalFilter = Exclude<KdjSignal, "none">;
export type KdjZone = "oversold" | "neutral" | "overbought" | "unknown";
export type KdjSignalZone = "low" | "middle" | "high" | "unknown";
export type KdjSignalZoneFilter = Exclude<KdjSignalZone, "unknown">;
export type KdjRecentWindow = "today" | "3d" | "5d";
export type KdjQuality = "ok" | "partial" | "insufficient" | "error";

export interface MarketSummary {
  total: number;
  rising: number;
  falling: number;
  flat: number;
  amount: number;
  market_status: "open" | "closed";
  last_updated_at: string | null;
  stale: boolean;
}

export interface StockQuote {
  market: Market;
  code: string;
  name: string;
  latest_price: number | null;
  change_percent: number | null;
  change_amount: number | null;
  open_price: number | null;
  high_price: number | null;
  low_price: number | null;
  previous_close: number | null;
  volume: number | null;
  amount: number | null;
  turnover_rate: number | null;
  total_market_cap: number | null;
  captured_at: string | null;
  quality_status: QualityStatus | null;
  macd_signal_type: MacdSignal | null;
  macd_signal_date: string | null;
  macd_recent_signal_days: number | null;
  macd_signal_label: string | null;
  macd_zero_axis: MacdZeroAxis | null;
  macd_quality: MacdQuality | null;
  kdj_signal_type: KdjSignal | null;
  kdj_signal_time: string | null;
  kdj_recent_signal_days: number | null;
  kdj_signal_label: string | null;
  kdj_signal_zone: KdjSignalZone | null;
  kdj_current_zone: KdjZone | null;
  kdj_quality: KdjQuality | null;
  macd_divergence_labels: MacdDivergenceFilter[];
}

export interface StockPage {
  items: StockQuote[];
  total: number;
  page: number;
  page_size: number;
}

export interface StockQuery {
  query?: string;
  market?: Market;
  page: number;
  pageSize: number;
  sortBy:
    | "code"
    | "latest_price"
    | "change_percent"
    | "amount"
    | "turnover_rate"
    | "total_market_cap";
  sortOrder: "asc" | "desc";
  macdSignal?: MacdSignalFilter;
  macdZeroAxis?: MacdZeroAxisFilter;
  macdRecentWindow?: MacdRecentWindow;
  kdjSignal?: KdjSignalFilter;
  kdjSignalZone?: KdjSignalZoneFilter;
  kdjRecentWindow?: KdjRecentWindow;
  macdDivergences?: MacdDivergenceFilter[];
  macdDivergenceCross?: MacdDivergenceCrossFilter;
  macdDivergenceRecentWindow?: MacdDivergenceRecentWindow;
}

export interface Bar {
  bar_time: string;
  acquired_at: string;
  open_price: number;
  high_price: number;
  low_price: number;
  close_price: number;
  volume: number;
  amount: number;
  is_complete: boolean;
  quality_status: QualityStatus;
}

export interface BarSeries {
  market: Market;
  code: string;
  period: BarPeriod;
  range: BarRange;
  adjustment: Adjustment;
  source: string | null;
  last_updated_at: string | null;
  fetch_quality_status: QualityStatus | null;
  last_fetch_at: string | null;
  fetch_raw_row_count: number | null;
  fetch_valid_row_count: number | null;
  fetch_invalid_row_count: number | null;
  items: Bar[];
}

export interface BarQuery {
  market: Market;
  code: string;
  period: BarPeriod;
  range: BarRange;
  adjustment: Adjustment;
}

export interface MacdPoint {
  bar_time: string;
  diff: number | null;
  dea: number | null;
  histogram: number | null;
  signal_type: MacdSignal;
  zero_axis: MacdZeroAxis;
  is_intraday: boolean;
  quality: MacdQuality;
}

export interface MacdSummary {
  calculated_at: string;
  market_time: string | null;
  diff: number | null;
  dea: number | null;
  histogram: number | null;
  signal_type: MacdSignal;
  signal_date: string | null;
  recent_signal_days: number | null;
  recent_signal_label: string;
  zero_axis: MacdZeroAxis;
  status: string;
  is_intraday: boolean;
  quality: MacdQuality;
}

export interface MacdDivergence {
  direction: "bottom" | "top";
  status: "forming" | "confirmed";
  anchor_one_time: string;
  anchor_one_price: number;
  anchor_one_diff: number;
  anchor_two_time: string;
  anchor_two_price: number;
  anchor_two_diff: number;
  pivot_time: string;
  pivot_price: number;
  pivot_diff: number;
  detected_at: string;
  updated_at: string;
  calculated_at: string;
  quality: MacdQuality;
  confirmed_at: string | null;
  corresponding_signal: MacdSignal;
  corresponding_signal_time: string | null;
  recent_days: number;
}

export interface MacdIndicator {
  market: Market;
  code: string;
  period: BarPeriod;
  summary: MacdSummary;
  divergences: MacdDivergence[];
  items: MacdPoint[];
}

export interface KdjPoint {
  bar_time: string;
  k_value: number | null;
  d_value: number | null;
  j_value: number | null;
  signal_type: KdjSignal;
  signal_zone: KdjSignalZone;
  current_zone: KdjZone;
  is_intraday: boolean;
  quality: KdjQuality;
}

export interface KdjSummary {
  calculated_at: string;
  market_time: string | null;
  k_value: number | null;
  d_value: number | null;
  j_value: number | null;
  current_zone: KdjZone;
  signal_type: KdjSignal;
  signal_time: string | null;
  signal_zone: KdjSignalZone;
  recent_signal_days: number | null;
  recent_signal_label: string;
  status: string;
  is_intraday: boolean;
  quality: KdjQuality;
}

export interface KdjIndicator {
  market: Market;
  code: string;
  period: BarPeriod;
  summary: KdjSummary;
  items: KdjPoint[];
}
