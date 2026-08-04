import type { BarSeries, MarketSummary, StockPage, StockQuote } from "../api/types";

export const summaryFixture: MarketSummary = {
  total: 5314,
  rising: 2918,
  falling: 2187,
  flat: 209,
  amount: 1_083_246_000_000,
  market_status: "open",
  last_updated_at: "2026-08-04T10:26:00+08:00",
  stale: false,
};

export const stockPageFixture: StockPage = {
  total: 2,
  page: 1,
  page_size: 50,
  items: [
    {
      market: "SH",
      code: "600519",
      name: "贵州茅台",
      latest_price: 1588.88,
      change_percent: 2.36,
      change_amount: 36.56,
      open_price: 1558.2,
      high_price: 1599.9,
      low_price: 1551.01,
      previous_close: 1552.32,
      volume: 3_821_100,
      amount: 6_058_000_000,
      turnover_rate: 0.3,
      total_market_cap: 1_995_000_000_000,
      captured_at: "2026-08-04T10:26:00+08:00",
      quality_status: "ok",
    },
    {
      market: "SZ",
      code: "000001",
      name: "平安银行",
      latest_price: 11.28,
      change_percent: -0.7,
      change_amount: -0.08,
      open_price: 11.36,
      high_price: 11.39,
      low_price: 11.25,
      previous_close: 11.36,
      volume: 45_312_000,
      amount: 512_000_000,
      turnover_rate: 0.23,
      total_market_cap: 218_900_000_000,
      captured_at: "2026-08-04T10:26:00+08:00",
      quality_status: "ok",
    },
  ],
};

export const stockDetailFixture: StockQuote = stockPageFixture.items[0];

export const dailyBarsFixture: BarSeries = {
  market: "SH",
  code: "600519",
  period: "1d",
  range: "60d",
  adjustment: "qfq",
  source: "akshare",
  last_updated_at: "2026-08-04T15:00:00+08:00",
  items: [
    {
      bar_time: "2026-08-01T15:00:00+08:00",
      acquired_at: "2026-08-04T15:10:00+08:00",
      open_price: 1320.2,
      high_price: 1350.08,
      low_price: 1315.04,
      close_price: 1346.06,
      volume: 2_880_000,
      amount: 3_840_000_000,
      is_complete: true,
      quality_status: "ok",
    },
  ],
};

export const todayBarsFixture: BarSeries = {
  ...dailyBarsFixture,
  period: "1m",
  range: "today",
  adjustment: "none",
  last_updated_at: "2026-08-04T10:31:00+08:00",
  items: [
    {
      bar_time: "2026-08-04T10:31:00+08:00",
      acquired_at: "2026-08-04T10:31:30+08:00",
      open_price: 1334.2,
      high_price: 1335.08,
      low_price: 1330.04,
      close_price: 1330.06,
      volume: 82_100,
      amount: 109_400_000,
      is_complete: false,
      quality_status: "partial",
    },
  ],
};
