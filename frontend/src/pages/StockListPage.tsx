import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { isAbortError, marketApi } from "../api/client";
import type {
  KdjRecentWindow,
  KdjSignalFilter,
  KdjSignalZoneFilter,
  MacdDivergenceFilter,
  MacdDivergenceRecentWindow,
  MacdRecentWindow,
  MacdSignalFilter,
  MacdZeroAxisFilter,
  Market,
  MarketSummary as MarketSummaryData,
  StockPage,
  StockQuery,
} from "../api/types";
import { MarketSummary } from "../components/MarketSummary";
import { StockTable } from "../components/StockTable";
import { usePolling } from "../hooks/usePolling";

const PAGE_SIZE = 50;
type MacdSignalSelection = "" | `${MacdSignalFilter}:${MacdRecentWindow}`;
const DEFAULT_SORT_BY: StockQuery["sortBy"] = "code";
const DEFAULT_SORT_ORDER: StockQuery["sortOrder"] = "asc";
const SCROLL_STORAGE_PREFIX = "stock-list-scroll:";

const DIVERGENCE_OPTIONS: Array<{ value: MacdDivergenceFilter; label: string; direction: "bottom" | "top"; confirmed: boolean }> = [
  { value: "bottom_forming", label: "底背离形成中", direction: "bottom", confirmed: false },
  { value: "bottom_confirmed", label: "底背离已确认", direction: "bottom", confirmed: true },
  { value: "top_forming", label: "顶背离形成中", direction: "top", confirmed: false },
  { value: "top_confirmed", label: "顶背离已确认", direction: "top", confirmed: true },
];
const MACD_SIGNAL_OPTIONS: Array<{ value: MacdSignalSelection; label: string }> = [
  { value: "", label: "全部信号" },
  { value: "golden_cross:today", label: "今日金叉" },
  { value: "golden_cross:3d", label: "近 3 日金叉" },
  { value: "golden_cross:5d", label: "近 5 日金叉" },
  { value: "death_cross:today", label: "今日死叉" },
  { value: "death_cross:3d", label: "近 3 日死叉" },
  { value: "death_cross:5d", label: "近 5 日死叉" },
];
const DIVERGENCE_TIME_OPTIONS: Array<{ value: MacdDivergenceRecentWindow | ""; label: string }> = [
  { value: "", label: "时间不限" },
  { value: "today", label: "今日" },
  { value: "3d", label: "近 3 日" },
  { value: "5d", label: "近 5 日" },
  { value: "10d", label: "近 10 日" },
  { value: "20d", label: "近 20 日" },
];

function parseMacdSignalSelection(value: MacdSignalSelection): {
  signal?: MacdSignalFilter;
  recentWindow?: MacdRecentWindow;
} {
  if (!value) return {};
  const [signal, recentWindow] = value.split(":") as [MacdSignalFilter, MacdRecentWindow];
  return { signal, recentWindow };
}

function isMarket(value: string | null): value is Market {
  return value === "SH" || value === "SZ" || value === "BJ";
}

function isSortBy(value: string | null): value is StockQuery["sortBy"] {
  return value === "code"
    || value === "latest_price"
    || value === "change_percent"
    || value === "amount"
    || value === "turnover_rate"
    || value === "total_market_cap";
}

function isSortOrder(value: string | null): value is StockQuery["sortOrder"] {
  return value === "asc" || value === "desc";
}

function isMacdSignal(value: string | null): value is MacdSignalFilter {
  return value === "golden_cross" || value === "death_cross";
}

function isMacdRecentWindow(value: string | null): value is MacdRecentWindow {
  return value === "today" || value === "3d" || value === "5d";
}

function isMacdDivergenceRecentWindow(value: string | null): value is MacdDivergenceRecentWindow {
  return value === "today" || value === "3d" || value === "5d" || value === "10d" || value === "20d";
}

function isMacdZeroAxis(value: string | null): value is MacdZeroAxisFilter {
  return value === "above" || value === "below";
}

function isMacdDivergence(value: string): value is MacdDivergenceFilter {
  return DIVERGENCE_OPTIONS.some((option) => option.value === value);
}

function numberFromParams(params: URLSearchParams, key: string, fallback: number) {
  const parsed = Number(params.get(key));
  if (!Number.isInteger(parsed) || parsed < 1) return fallback;
  return parsed;
}

function macdSignalSelectionFromParams(params: URLSearchParams): MacdSignalSelection {
  const signal = params.get("macd_signal");
  if (!isMacdSignal(signal)) return "";
  const recentWindow = params.get("macd_recent_window");
  return `${signal}:${isMacdRecentWindow(recentWindow) ? recentWindow : "5d"}`;
}

function macdDivergencesFromParams(params: URLSearchParams) {
  const selected = new Set(params.getAll("macd_divergences").filter(isMacdDivergence));
  return DIVERGENCE_OPTIONS
    .map((option) => option.value)
    .filter((value) => selected.has(value));
}

function stockListParamsFromState({
  query,
  market,
  page,
  sortBy,
  sortOrder,
  macdSignalSelection,
  macdZeroAxis,
  macdDivergences,
  macdDivergenceRecentWindow,
}: {
  query: string;
  market: Market | "";
  page: number;
  sortBy: StockQuery["sortBy"];
  sortOrder: StockQuery["sortOrder"];
  macdSignalSelection: MacdSignalSelection;
  macdZeroAxis: MacdZeroAxisFilter | "";
  macdDivergences: MacdDivergenceFilter[];
  macdDivergenceRecentWindow: MacdDivergenceRecentWindow | "";
}) {
  const params = new URLSearchParams();
  if (query) params.set("query", query);
  if (market) params.set("market", market);
  if (page > 1) params.set("page", String(page));
  if (sortBy !== DEFAULT_SORT_BY) params.set("sort_by", sortBy);
  if (sortOrder !== DEFAULT_SORT_ORDER) params.set("sort_order", sortOrder);
  const macdSignal = parseMacdSignalSelection(macdSignalSelection);
  if (macdSignal.signal) params.set("macd_signal", macdSignal.signal);
  if (macdSignal.recentWindow) params.set("macd_recent_window", macdSignal.recentWindow);
  if (macdZeroAxis) params.set("macd_zero_axis", macdZeroAxis);
  macdDivergences.forEach((value) => params.append("macd_divergences", value));
  if (macdDivergenceRecentWindow) {
    params.set("macd_divergence_recent_window", macdDivergenceRecentWindow);
  }
  return params;
}

function formatUpdateTime(value: string | null) {
  if (!value) return "等待首次行情";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(date);
}

function readableError(error: unknown) {
  return error instanceof Error ? `行情加载失败：${error.message}` : "行情加载失败，请稍后重试";
}

export function StockListPage() {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialQuery = searchParams.get("query")?.trim() ?? "";
  const initialMarket = searchParams.get("market");
  const initialSortBy = searchParams.get("sort_by");
  const initialSortOrder = searchParams.get("sort_order");
  const initialMacdZeroAxis = searchParams.get("macd_zero_axis");
  const initialMacdDivergenceRecentWindow = searchParams.get("macd_divergence_recent_window");
  const [searchInput, setSearchInput] = useState(initialQuery);
  const [query, setQuery] = useState(initialQuery);
  const [market, setMarket] = useState<Market | "">(isMarket(initialMarket) ? initialMarket : "");
  const [page, setPage] = useState(() => numberFromParams(searchParams, "page", 1));
  const [sortBy, setSortBy] = useState<StockQuery["sortBy"]>(isSortBy(initialSortBy) ? initialSortBy : DEFAULT_SORT_BY);
  const [sortOrder, setSortOrder] = useState<StockQuery["sortOrder"]>(
    isSortOrder(initialSortOrder) ? initialSortOrder : DEFAULT_SORT_ORDER,
  );
  const [macdSignalSelection, setMacdSignalSelection] = useState<MacdSignalSelection>(
    () => macdSignalSelectionFromParams(searchParams),
  );
  const [macdZeroAxis, setMacdZeroAxis] = useState<MacdZeroAxisFilter | "">(
    isMacdZeroAxis(initialMacdZeroAxis) ? initialMacdZeroAxis : "",
  );
  const [macdDivergences, setMacdDivergences] = useState<MacdDivergenceFilter[]>(
    () => macdDivergencesFromParams(searchParams),
  );
  const [macdDivergenceRecentWindow, setMacdDivergenceRecentWindow] = useState<MacdDivergenceRecentWindow | "">(
    isMacdDivergenceRecentWindow(initialMacdDivergenceRecentWindow) ? initialMacdDivergenceRecentWindow : "",
  );
  const [divergenceMenuOpen, setDivergenceMenuOpen] = useState(false);
  const [kdjSignal, setKdjSignal] = useState<KdjSignalFilter | "">("");
  const [kdjSignalZone, setKdjSignalZone] = useState<KdjSignalZoneFilter | "">("");
  const [kdjRecentWindow, setKdjRecentWindow] = useState<KdjRecentWindow | "">("");
  const [summary, setSummary] = useState<MarketSummaryData | null>(null);
  const [stockPage, setStockPage] = useState<StockPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const mounted = useRef(true);
  const restoredScrollKey = useRef<string | null>(null);
  const selectedMacdSignal = parseMacdSignalSelection(macdSignalSelection);

  useEffect(() => {
    const id = window.setTimeout(() => {
      setPage(1);
      setQuery(searchInput.trim());
    }, 250);
    return () => window.clearTimeout(id);
  }, [searchInput]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestSequence.current += 1;
    };
  }, []);

  useEffect(() => {
    const nextParams = stockListParamsFromState({
      query,
      market,
      page,
      sortBy,
      sortOrder,
      macdSignalSelection,
      macdZeroAxis,
      macdDivergences,
      macdDivergenceRecentWindow,
    });
    if (nextParams.toString() !== searchParams.toString()) {
      setSearchParams(nextParams, { replace: true });
    }
  }, [
    macdDivergences,
    macdDivergenceRecentWindow,
    macdSignalSelection,
    macdZeroAxis,
    market,
    page,
    query,
    searchParams,
    setSearchParams,
    sortBy,
    sortOrder,
  ]);

  useEffect(() => {
    const scrollKey = `${SCROLL_STORAGE_PREFIX}${location.pathname}${location.search}`;
    return () => {
      window.sessionStorage.setItem(scrollKey, String(window.scrollY));
    };
  }, [location.pathname, location.search]);

  const load = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++requestSequence.current;
    setLoading(true);
    setError(null);
    try {
      const [nextSummary, nextPage] = await Promise.all([
        marketApi.getSummary({ signal }),
        marketApi.getStocks({
          query: query || undefined,
          market: market || undefined,
          page,
          pageSize: PAGE_SIZE,
          sortBy,
          sortOrder,
          macdSignal: selectedMacdSignal.signal,
          macdZeroAxis: macdZeroAxis || undefined,
          macdRecentWindow: selectedMacdSignal.recentWindow,
          kdjSignal: kdjSignal || undefined,
          kdjSignalZone: kdjSignalZone || undefined,
          kdjRecentWindow: kdjRecentWindow || undefined,
          macdDivergences: macdDivergences.length > 0 ? macdDivergences : undefined,
          macdDivergenceRecentWindow: macdDivergenceRecentWindow || undefined,
        }, { signal }),
      ]);
      if (!mounted.current || sequence !== requestSequence.current) return;
      setSummary(nextSummary);
      setStockPage(nextPage);
    } catch (loadError) {
      if (!mounted.current || sequence !== requestSequence.current) return;
      if (isAbortError(loadError)) return;
      setError(readableError(loadError));
    } finally {
      if (mounted.current && sequence === requestSequence.current) setLoading(false);
    }
  }, [
    kdjRecentWindow,
    kdjSignal,
    kdjSignalZone,
    macdDivergences,
    macdDivergenceRecentWindow,
    macdSignalSelection,
    macdZeroAxis,
    market,
    page,
    query,
    sortBy,
    sortOrder,
  ]);

  usePolling(load, 60_000);

  useEffect(() => {
    if (stockPage === null) return;
    const scrollKey = `${SCROLL_STORAGE_PREFIX}${location.pathname}${location.search}`;
    if (restoredScrollKey.current === scrollKey) return;
    restoredScrollKey.current = scrollKey;
    const savedTop = Number(window.sessionStorage.getItem(scrollKey));
    if (Number.isFinite(savedTop) && savedTop > 0) {
      window.scrollTo({ top: savedTop, left: 0, behavior: "auto" });
    }
  }, [stockPage, location.pathname, location.search]);

  const handleSort = (field: StockQuery["sortBy"]) => {
    setPage(1);
    if (field === sortBy) {
      setSortOrder((current) => (current === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(field);
      setSortOrder("desc");
    }
  };

  const toggleDivergence = (value: MacdDivergenceFilter) => {
    setMacdDivergences((current) => DIVERGENCE_OPTIONS
      .map((option) => option.value)
      .filter((item) => (item === value ? !current.includes(item) : current.includes(item))));
    setPage(1);
  };

  const totalPages = stockPage ? Math.max(1, Math.ceil(stockPage.total / PAGE_SIZE)) : 1;
  const hasData = stockPage !== null;
  const hasIndicatorFilters = macdSignalSelection !== ""
    || macdZeroAxis !== ""
    || macdDivergences.length > 0
    || macdDivergenceRecentWindow !== ""
    || kdjSignal !== ""
    || kdjSignalZone !== ""
    || kdjRecentWindow !== "";
  const divergenceButtonText = macdDivergences.length > 0
    ? `MACD 背离：已选 ${macdDivergences.length} 项`
    : "MACD 背离：不限";
  const marketStatus = summary?.market_status === "open"
    ? "交易中"
    : summary?.market_status === "closed"
      ? "已收盘"
      : "正在确认";

  return (
    <section className="stock-list-page">
      <header className="page-header">
        <div>
          <span className="page-kicker">MARKET / A-SHARE</span>
          <h1>全市场行情</h1>
          <p>浏览沪深北全部股票，每分钟同步当前可见页。</p>
        </div>
        <div className="market-meta" aria-live="polite">
          <span className={summary?.market_status === "open" ? "market-state is-open" : "market-state"}>
            {marketStatus}
          </span>
          <span>最后更新 {formatUpdateTime(summary?.last_updated_at ?? null)}</span>
          {summary?.stale && <strong>数据可能已过期</strong>}
        </div>
      </header>

      <MarketSummary summary={summary} />

      <section className="market-panel" aria-label="全部股票">
        <div className="stock-toolbar">
          <label className="search-field">
            <span className="sr-only">搜索股票</span>
            <svg aria-hidden="true" viewBox="0 0 20 20">
              <circle cx="8.5" cy="8.5" r="5.5" />
              <path d="m12.5 12.5 4 4" />
            </svg>
            <input
              value={searchInput}
              placeholder="搜索股票代码或名称"
              onChange={(event) => setSearchInput(event.target.value)}
            />
          </label>

          <label className="market-filter">
            <span>市场</span>
            <select
              aria-label="市场筛选"
              value={market}
              onChange={(event) => {
                setMarket(event.target.value as Market | "");
                setPage(1);
              }}
            >
              <option value="">全部市场</option>
              <option value="SH">沪市 SH</option>
              <option value="SZ">深市 SZ</option>
              <option value="BJ">北交所 BJ</option>
            </select>
          </label>

          <span className="result-count data-value">
            {stockPage ? `${stockPage.total.toLocaleString("zh-CN")} 只股票` : "正在读取股票"}
          </span>
          {loading && hasData && <span className="refreshing" role="status">正在刷新…</span>}
        </div>

        <div className="indicator-filter-bar" aria-label="MACD 指标筛选">
          <label className="indicator-filter">
            <span className="sr-only">日 K MACD 信号</span>
            <select
              aria-label="日 K MACD 信号"
              value={macdSignalSelection}
              onChange={(event) => {
                setMacdSignalSelection(event.target.value as MacdSignalSelection);
                setPage(1);
              }}
            >
              {MACD_SIGNAL_OPTIONS.map((option) => (
                <option key={option.value || "all"} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="indicator-filter">
            <span>零轴位置</span>
            <select
              aria-label="零轴位置"
              value={macdZeroAxis}
              onChange={(event) => {
                setMacdZeroAxis(event.target.value as MacdZeroAxisFilter | "");
                setPage(1);
              }}
            >
              <option value="">零轴不限</option>
              <option value="above">零轴线上</option>
              <option value="below">零轴线下</option>
            </select>
          </label>
          <div className="divergence-select">
            <button
              type="button"
              className="divergence-select-trigger"
              aria-expanded={divergenceMenuOpen}
              onClick={() => setDivergenceMenuOpen((open) => !open)}
            >
              {divergenceButtonText}
            </button>
            {divergenceMenuOpen && (
              <div className="divergence-menu" role="group" aria-label="MACD 背离选项">
                {DIVERGENCE_OPTIONS.map((option) => (
                  <label
                    key={option.value}
                    className={`divergence-option is-${option.direction}${option.confirmed ? " is-confirmed" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={macdDivergences.includes(option.value)}
                      onChange={() => toggleDivergence(option.value)}
                    />
                    <span>{option.label}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
          <label className="indicator-filter">
            <span>背离时间</span>
            <select
              aria-label="背离时间"
              value={macdDivergenceRecentWindow}
              onChange={(event) => {
                setMacdDivergenceRecentWindow(event.target.value as MacdDivergenceRecentWindow | "");
                setPage(1);
              }}
            >
              {DIVERGENCE_TIME_OPTIONS.map((option) => (
                <option key={option.value || "all"} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="indicator-filter-bar is-kdj" aria-label="KDJ 指标筛选">
          <label className="indicator-filter">
            <span className="sr-only">日 K KDJ 信号</span>
            <select
              aria-label="日 K KDJ 信号"
              value={kdjSignal}
              onChange={(event) => {
                setKdjSignal(event.target.value as KdjSignalFilter | "");
                setPage(1);
              }}
            >
              <option value="">全部信号</option>
              <option value="golden_cross">金叉</option>
              <option value="death_cross">死叉</option>
            </select>
          </label>
          <label className="indicator-filter">
            <span>交叉区域</span>
            <select
              aria-label="KDJ 交叉区域"
              value={kdjSignalZone}
              onChange={(event) => {
                setKdjSignalZone(event.target.value as KdjSignalZoneFilter | "");
                setPage(1);
              }}
            >
              <option value="">区域不限</option>
              <option value="low">低位</option>
              <option value="middle">中位</option>
              <option value="high">高位</option>
            </select>
          </label>
          <label className="indicator-filter">
            <span>出现时间</span>
            <select
              aria-label="KDJ 出现时间"
              value={kdjRecentWindow}
              onChange={(event) => {
                setKdjRecentWindow(event.target.value as KdjRecentWindow | "");
                setPage(1);
              }}
            >
              <option value="">时间不限</option>
              <option value="today">今日</option>
              <option value="3d">近 3 日</option>
              <option value="5d">近 5 日</option>
            </select>
          </label>
          <button
            type="button"
            className="indicator-filter-clear"
            disabled={!hasIndicatorFilters || loading}
            onClick={() => {
              setMacdSignalSelection("");
              setMacdZeroAxis("");
              setKdjSignal("");
              setKdjSignalZone("");
              setKdjRecentWindow("");
              setMacdDivergences([]);
              setMacdDivergenceRecentWindow("");
              setDivergenceMenuOpen(false);
              setPage(1);
            }}
          >
            清空指标
          </button>
        </div>

        {error && (
          <div className="error-state" role="alert">
            <div>
              <strong>暂时无法读取行情</strong>
              <p>{error}</p>
            </div>
            <button type="button" onClick={() => void load()}>重新加载</button>
          </div>
        )}

        {!hasData && loading ? (
          <div className="empty-state" role="status">正在加载全市场行情…</div>
        ) : stockPage?.items.length === 0 ? (
          <div className="empty-state">
            <strong>没有找到匹配的股票</strong>
            <span>请尝试更换代码、名称或市场。</span>
          </div>
        ) : stockPage ? (
          <StockTable
            stocks={stockPage.items}
            sortBy={sortBy}
            sortOrder={sortOrder}
            onSort={handleSort}
          />
        ) : null}

        {stockPage && (
          <footer className="pagination" aria-label="分页">
            <span>
              第 <strong className="data-value">{page}</strong> / {totalPages} 页
            </span>
            <div>
              <button type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}>
                上一页
              </button>
              <button type="button" disabled={page >= totalPages || loading} onClick={() => setPage((value) => value + 1)}>
                下一页
              </button>
            </div>
          </footer>
        )}
      </section>
    </section>
  );
}
