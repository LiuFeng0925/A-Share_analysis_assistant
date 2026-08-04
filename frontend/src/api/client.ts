import type {
  BarQuery,
  BarSeries,
  Market,
  MarketSummary,
  StockPage,
  StockQuery,
  StockQuote,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`);
  } catch {
    throw new Error("无法连接行情服务，请检查后端是否已启动");
  }

  if (!response.ok) {
    throw new Error(`行情接口失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const marketApi = {
  getSummary: () => request<MarketSummary>("/api/market/summary"),
  getStocks: (params: StockQuery) => {
    const query = new URLSearchParams();
    if (params.query) query.set("query", params.query);
    if (params.market) query.set("market", params.market);
    query.set("page", String(params.page));
    query.set("page_size", String(params.pageSize));
    query.set("sort_by", params.sortBy);
    query.set("sort_order", params.sortOrder);
    return request<StockPage>(`/api/market/stocks?${query}`);
  },
  getStock: (market: Market, code: string) =>
    request<StockQuote>(`/api/stocks/${market}/${encodeURIComponent(code)}`),
  getBars: (params: BarQuery) => {
    const query = new URLSearchParams({
      period: params.period,
      range: params.range,
      adjustment: params.adjustment,
    });
    return request<BarSeries>(
      `/api/stocks/${params.market}/${encodeURIComponent(params.code)}/bars?${query}`,
    );
  },
};
