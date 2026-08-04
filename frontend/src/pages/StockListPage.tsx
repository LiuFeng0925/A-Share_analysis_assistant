import { useCallback, useEffect, useRef, useState } from "react";
import { isAbortError, marketApi } from "../api/client";
import type { Market, MarketSummary as MarketSummaryData, StockPage, StockQuery } from "../api/types";
import { MarketSummary } from "../components/MarketSummary";
import { StockTable } from "../components/StockTable";
import { usePolling } from "../hooks/usePolling";

const PAGE_SIZE = 50;

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
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [market, setMarket] = useState<Market | "">("");
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState<StockQuery["sortBy"]>("code");
  const [sortOrder, setSortOrder] = useState<StockQuery["sortOrder"]>("asc");
  const [summary, setSummary] = useState<MarketSummaryData | null>(null);
  const [stockPage, setStockPage] = useState<StockPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const mounted = useRef(true);

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
  }, [market, page, query, sortBy, sortOrder]);

  usePolling(load, 60_000);

  const handleSort = (field: StockQuery["sortBy"]) => {
    setPage(1);
    if (field === sortBy) {
      setSortOrder((current) => (current === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(field);
      setSortOrder("desc");
    }
  };

  const totalPages = stockPage ? Math.max(1, Math.ceil(stockPage.total / PAGE_SIZE)) : 1;
  const hasData = stockPage !== null;
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
