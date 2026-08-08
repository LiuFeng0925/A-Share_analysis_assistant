import type {
  BarQuery,
  BarPeriod,
  BarSeries,
  KdjIndicator,
  MacdIndicator,
  Market,
  MarketSummary,
  StockPage,
  StockQuery,
  StockQuote,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const DEFAULT_TIMEOUT_MS = 10_000;

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function abortError(): DOMException {
  return new DOMException("请求已取消", "AbortError");
}

class ApiHttpError extends Error {}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const abortFromCaller = () => controller.abort(options.signal?.reason);
  if (options.signal?.aborted) abortFromCaller();
  else options.signal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, { signal: controller.signal });
    if (!response.ok) {
      throw new ApiHttpError(`行情接口失败：${response.status}`);
    }
    return await response.json() as T;
  } catch (error) {
    if (options.signal?.aborted) throw abortError();
    if (timedOut) throw new Error("行情请求超时，请稍后重试");
    if (error instanceof ApiHttpError) throw error;
    if (isAbortError(error)) throw error;
    throw new Error("无法连接行情服务，请检查后端是否已启动");
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abortFromCaller);
  }
}

export const marketApi = {
  getSummary: (options?: RequestOptions) => request<MarketSummary>("/api/market/summary", options),
  getStocks: (params: StockQuery, options?: RequestOptions) => {
    const query = new URLSearchParams();
    if (params.query) query.set("query", params.query);
    if (params.market) query.set("market", params.market);
    query.set("page", String(params.page));
    query.set("page_size", String(params.pageSize));
    query.set("sort_by", params.sortBy);
    query.set("sort_order", params.sortOrder);
    if (params.macdSignal) query.set("macd_signal", params.macdSignal);
    if (params.macdZeroAxis) query.set("macd_zero_axis", params.macdZeroAxis);
    if (params.macdRecentWindow) query.set("macd_recent_window", params.macdRecentWindow);
    if (params.kdjSignal) query.set("kdj_signal", params.kdjSignal);
    if (params.kdjSignalZone) query.set("kdj_signal_zone", params.kdjSignalZone);
    if (params.kdjRecentWindow) query.set("kdj_recent_window", params.kdjRecentWindow);
    params.macdDivergences?.forEach((value) => query.append("macd_divergences", value));
    if (params.macdDivergenceCross) {
      query.set("macd_divergence_cross", params.macdDivergenceCross);
    }
    if (params.macdDivergenceRecentWindow) {
      query.set("macd_divergence_recent_window", params.macdDivergenceRecentWindow);
    }
    return request<StockPage>(`/api/market/stocks?${query}`, options);
  },
  getStock: (market: Market, code: string, options?: RequestOptions) =>
    request<StockQuote>(`/api/stocks/${market}/${encodeURIComponent(code)}`, options),
  getBars: (params: BarQuery, options?: RequestOptions) => {
    const query = new URLSearchParams({
      period: params.period,
      range: params.range,
      adjustment: params.adjustment,
    });
    return request<BarSeries>(
      `/api/stocks/${params.market}/${encodeURIComponent(params.code)}/bars?${query}`,
      options,
    );
  },
  getMacdIndicator: (
    market: Market,
    code: string,
    period: BarPeriod = "1d",
    options?: RequestOptions,
  ) =>
    request<MacdIndicator>(
      `/api/stocks/${market}/${encodeURIComponent(code)}/indicators/macd?period=${period}`,
      options,
    ),
  getKdjIndicator: (
    market: Market,
    code: string,
    period: BarPeriod = "1d",
    options?: RequestOptions,
  ) =>
    request<KdjIndicator>(
      `/api/stocks/${market}/${encodeURIComponent(code)}/indicators/kdj?period=${period}`,
      options,
    ),
};
