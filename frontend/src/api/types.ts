export type Market = "SH" | "SZ" | "BJ";
export type QualityStatus = "ok" | "partial" | "stale" | "error";
export type BarPeriod = "1m" | "5m" | "15m" | "30m" | "60m" | "1d" | "1w" | "1mo";
export type BarRange = "today" | "5d" | "60d" | "6mo" | "ytd" | "1y" | "5y" | "all";
export type Adjustment = "none" | "qfq" | "hfq";

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
  items: Bar[];
}

export interface BarQuery {
  market: Market;
  code: string;
  period: BarPeriod;
  range: BarRange;
  adjustment: Adjustment;
}
