export type Market = "SH" | "SZ" | "BJ";
export type QualityStatus = "ok" | "partial" | "stale" | "error";

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
  open_price: number;
  high_price: number;
  low_price: number;
  close_price: number;
  volume: number;
  amount: number;
  is_complete: boolean;
}

export interface BarSeries {
  market: Market;
  code: string;
  period: string;
  range: string;
  adjustment: string;
  source: string | null;
  last_updated_at: string | null;
  items: Bar[];
}
