import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { isAbortError, marketApi } from "../api/client";
import type {
  Adjustment,
  BarPeriod,
  BarRange,
  BarSeries,
  MacdIndicator,
  Market,
  MarketSummary,
  QualityStatus,
  StockQuote,
} from "../api/types";
import { KlineChart } from "../components/KlineChart";
import { usePolling } from "../hooks/usePolling";
import { formatMarketNumber, formatShanghaiDateTime, isFiniteNumber } from "../utils/marketFormat";

interface PeriodOption {
  label: string;
  period: BarPeriod;
  range: BarRange;
  adjustment: Adjustment;
}

const PERIODS = [
  { label: "今日", period: "1m", range: "today", adjustment: "none" },
  { label: "1分", period: "1m", range: "5d", adjustment: "none" },
  { label: "5分", period: "5m", range: "60d", adjustment: "qfq" },
  { label: "15分", period: "15m", range: "60d", adjustment: "qfq" },
  { label: "30分", period: "30m", range: "60d", adjustment: "qfq" },
  { label: "60分", period: "60m", range: "60d", adjustment: "qfq" },
  { label: "日K", period: "1d", range: "60d", adjustment: "qfq" },
  { label: "周K", period: "1w", range: "1y", adjustment: "qfq" },
  { label: "月K", period: "1mo", range: "5y", adjustment: "qfq" },
] as const satisfies readonly PeriodOption[];

const DEFAULT_PERIOD = PERIODS[6];
const MARKETS = new Set<Market>(["SH", "SZ", "BJ"]);

function asMarket(value: string | undefined): Market | null {
  return value && MARKETS.has(value as Market) ? (value as Market) : null;
}

function formatCompact(value: number | null | undefined) {
  if (!isFiniteNumber(value)) return "—";
  if (Math.abs(value) >= 1_000_000_000_000) return `${formatMarketNumber(value / 1_000_000_000_000, 3)} 万亿`;
  if (Math.abs(value) >= 100_000_000) return `${formatMarketNumber(value / 100_000_000)} 亿`;
  if (Math.abs(value) >= 10_000) return `${formatMarketNumber(value / 10_000)} 万`;
  return formatMarketNumber(value, 0);
}

function movement(value: number | null | undefined) {
  if (!isFiniteNumber(value)) return { className: "", label: "未知", prefix: "" };
  if (value === 0) return { className: "", label: "平盘", prefix: "" };
  return value > 0
    ? { className: "up", label: "上涨", prefix: "+" }
    : { className: "down", label: "下跌", prefix: "" };
}

function formatSigned(value: number | null | undefined, suffix = "") {
  const direction = movement(value);
  return isFiniteNumber(value)
    ? `${direction.prefix}${formatMarketNumber(value)}${suffix}`
    : "—";
}

function qualityMeta(status: QualityStatus | null) {
  const values: Record<QualityStatus, { label: string; className: string }> = {
    ok: { label: "质量正常", className: "is-ok" },
    partial: { label: "数据不完整", className: "is-warning" },
    stale: { label: "数据已过期", className: "is-warning" },
    error: { label: "数据异常", className: "is-error" },
  };
  return status ? values[status] : { label: "质量未知", className: "is-warning" };
}

function formatIndicatorValue(value: number | null | undefined) {
  return isFiniteNumber(value) ? formatMarketNumber(value) : "—";
}

function zeroAxisLabel(value: MacdIndicator["summary"]["zero_axis"] | undefined) {
  if (value === "above") return "零轴线上";
  if (value === "below") return "零轴线下";
  return "零轴未知";
}

function macdSignalTone(value: MacdIndicator["summary"]["signal_type"] | undefined) {
  if (value === "golden_cross") return "is-golden";
  if (value === "death_cross") return "is-death";
  return "is-muted";
}

function macdQualityLabel(value: MacdIndicator["summary"]["quality"] | undefined) {
  const labels = {
    ok: "计算正常",
    partial: "盘中动态",
    insufficient: "数据不足",
    error: "计算异常",
  } as const;
  return value ? labels[value] : "等待计算";
}

function periodKey(market: Market, code: string, option: PeriodOption) {
  return `${market}/${code}/${option.period}/${option.range}/${option.adjustment}`;
}

function indicatorPeriodLabel(option: PeriodOption) {
  if (option.label === "今日") return "今日 1分";
  return option.label;
}

function readableError(error: unknown, scope: "股票" | "K 线") {
  const message = error instanceof Error ? error.message : "未知错误";
  return `${scope}加载失败：${message}`;
}

function StockSwitcher({ onSelect }: { onSelect: (stock: StockQuote) => void }) {
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<StockQuote[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [open, setOpen] = useState(false);
  const requestSequence = useRef(0);

  useEffect(() => {
    const keyword = query.trim();
    const sequence = ++requestSequence.current;
    const controller = new AbortController();
    if (!keyword) {
      setCandidates([]);
      setLoading(false);
      setError(null);
      setActiveIndex(-1);
      return () => controller.abort();
    }

    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await marketApi.getStocks({
          query: keyword,
          page: 1,
          pageSize: 10,
          sortBy: "code",
          sortOrder: "asc",
        }, { signal: controller.signal });
        if (sequence !== requestSequence.current) return;
        setCandidates(result.items);
        setActiveIndex(result.items.length > 0 ? 0 : -1);
        setOpen(true);
      } catch (searchError) {
        if (sequence !== requestSequence.current || isAbortError(searchError)) return;
        setCandidates([]);
        setActiveIndex(-1);
        setError("暂时无法搜索股票，请稍后重试");
        setOpen(true);
      } finally {
        if (sequence === requestSequence.current) setLoading(false);
      }
    }, 220);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const selectCandidate = (candidate: StockQuote) => {
    setQuery("");
    setCandidates([]);
    setOpen(false);
    onSelect(candidate);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (candidates.length === 0) return;
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((current) => {
        if (current < 0) return direction > 0 ? 0 : candidates.length - 1;
        return (current + direction + candidates.length) % candidates.length;
      });
      setOpen(true);
      return;
    }
    if (event.key === "Enter" && open) {
      const candidate = candidates[activeIndex] ?? candidates[0];
      if (candidate) {
        event.preventDefault();
        selectCandidate(candidate);
      }
    }
  };

  const listboxId = "stock-switcher-options";
  return (
    <div className="stock-switcher">
      <label>
        <span className="sr-only">搜索其他股票</span>
        <input
          role="combobox"
          aria-label="搜索其他股票"
          aria-autocomplete="list"
          aria-controls={listboxId}
          aria-expanded={open && Boolean(query.trim())}
          aria-activedescendant={activeIndex >= 0 ? `stock-switch-option-${activeIndex}` : undefined}
          value={query}
          placeholder="输入代码或名称切换股票"
          onChange={(event) => {
            const nextQuery = event.target.value;
            const hasKeyword = Boolean(nextQuery.trim());
            requestSequence.current += 1;
            setQuery(nextQuery);
            setCandidates([]);
            setActiveIndex(-1);
            setError(null);
            setLoading(hasKeyword);
            setOpen(hasKeyword);
          }}
          onFocus={() => setOpen(Boolean(query.trim()))}
          onKeyDown={handleKeyDown}
        />
      </label>
      {open && query.trim() && (
        <div className="stock-switch-popover">
          {loading ? (
            <span className="stock-switch-state" role="status">正在搜索股票…</span>
          ) : error ? (
            <span className="stock-switch-state is-error" role="alert">{error}</span>
          ) : candidates.length === 0 ? (
            <span className="stock-switch-state">没有匹配的股票</span>
          ) : (
            <ul id={listboxId} role="listbox" aria-label="股票搜索结果">
              {candidates.map((candidate, index) => (
                <li
                  id={`stock-switch-option-${index}`}
                  key={`${candidate.market}-${candidate.code}`}
                  role="option"
                  aria-label={`${candidate.name} ${candidate.code} ${candidate.market}`}
                  aria-selected={activeIndex === index}
                  onMouseEnter={() => setActiveIndex(index)}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    selectCandidate(candidate);
                  }}
                >
                  <strong>{candidate.name}</strong>
                  <span className="data-value">{candidate.code}</span>
                  <small>{candidate.market}</small>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export function StockDetailPage() {
  const params = useParams();
  const navigate = useNavigate();
  const market = useMemo(() => asMarket(params.market), [params.market]);
  const code = params.code?.trim() ?? "";
  const [selectedPeriod, setSelectedPeriod] = useState<(typeof PERIODS)[number]>(DEFAULT_PERIOD);
  const [stock, setStock] = useState<StockQuote | null>(null);
  const [marketSummary, setMarketSummary] = useState<MarketSummary | null>(null);
  const [bars, setBars] = useState<BarSeries | null>(null);
  const [macd, setMacd] = useState<MacdIndicator | null>(null);
  const [loadedBarsKey, setLoadedBarsKey] = useState<string | null>(null);
  const [stockLoading, setStockLoading] = useState(Boolean(market && code));
  const [macdLoading, setMacdLoading] = useState(Boolean(market && code));
  const [barsLoadingKey, setBarsLoadingKey] = useState<string | null>(null);
  const [barsRefreshingKey, setBarsRefreshingKey] = useState<string | null>(null);
  const [stockError, setStockError] = useState<string | null>(null);
  const [macdError, setMacdError] = useState<string | null>(null);
  const [barsError, setBarsError] = useState<{ key: string; message: string } | null>(null);
  const [barsRefreshError, setBarsRefreshError] = useState<{ key: string; message: string } | null>(null);
  const stockRequestSequence = useRef(0);
  const barsRequestSequence = useRef(0);
  const macdRequestSequence = useRef(0);
  const summaryRequestSequence = useRef(0);
  const barsRef = useRef<BarSeries | null>(null);
  const loadedBarsKeyRef = useRef<string | null>(null);
  const activeBarsRequestKeyRef = useRef<string | null>(null);
  const selectedBarsKeyRef = useRef<string | null>(null);
  const mounted = useRef(true);
  const selectedBarsKey = market && code ? periodKey(market, code, selectedPeriod) : null;
  selectedBarsKeyRef.current = selectedBarsKey;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      stockRequestSequence.current += 1;
      barsRequestSequence.current += 1;
      macdRequestSequence.current += 1;
    };
  }, []);

  const loadStock = useCallback(async (signal?: AbortSignal) => {
    if (!market || !code) return;
    const sequence = ++stockRequestSequence.current;
    setStockLoading(true);
    setStockError(null);
    setStock(null);
    try {
      const nextStock = await marketApi.getStock(market, code, { signal });
      if (mounted.current && sequence === stockRequestSequence.current) setStock(nextStock);
    } catch (error) {
      if (mounted.current && sequence === stockRequestSequence.current) {
        if (isAbortError(error)) return;
        setStock(null);
        setStockError(readableError(error, "股票"));
      }
    } finally {
      if (mounted.current && sequence === stockRequestSequence.current) setStockLoading(false);
    }
  }, [code, market]);

  const loadMarketSummary = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++summaryRequestSequence.current;
    try {
      const summary = await marketApi.getSummary({ signal });
      if (mounted.current && sequence === summaryRequestSequence.current) {
        setMarketSummary(summary);
      }
      return summary;
    } catch (error) {
      if (isAbortError(error)) return null;
      return null;
    }
  }, []);

  const loadMacd = useCallback(async (signal?: AbortSignal) => {
    if (!market || !code) return;
    const sequence = ++macdRequestSequence.current;
    setMacdLoading(true);
    setMacdError(null);
    setMacd(null);
    try {
      const nextMacd = await marketApi.getMacdIndicator(
        market,
        code,
        selectedPeriod.period,
        { signal },
      );
      if (mounted.current && sequence === macdRequestSequence.current) {
        setMacd(nextMacd);
      }
    } catch (error) {
      if (mounted.current && sequence === macdRequestSequence.current) {
        if (isAbortError(error)) return;
        setMacd(null);
        setMacdError(error instanceof Error ? `MACD 加载失败：${error.message}` : "MACD 加载失败");
      }
    } finally {
      if (mounted.current && sequence === macdRequestSequence.current) {
        setMacdLoading(false);
      }
    }
  }, [code, market, selectedPeriod]);

  const loadBars = useCallback(async (signal?: AbortSignal) => {
    if (!market || !code) return;
    const key = periodKey(market, code, selectedPeriod);
    const keepsCurrentChart = loadedBarsKeyRef.current === key && barsRef.current !== null;
    const sequence = ++barsRequestSequence.current;
    activeBarsRequestKeyRef.current = key;
    setBarsRefreshError((current) => current?.key === key ? null : current);
    if (keepsCurrentChart) {
      setBarsRefreshingKey(key);
    } else {
      setBarsLoadingKey(key);
      setBarsError((current) => current?.key === key ? null : current);
    }
    try {
      const nextBars = await marketApi.getBars({
        market,
        code,
        period: selectedPeriod.period,
        range: selectedPeriod.range,
        adjustment: selectedPeriod.adjustment,
      }, { signal });
      if (mounted.current && sequence === barsRequestSequence.current) {
        barsRef.current = nextBars;
        loadedBarsKeyRef.current = key;
        setBars(nextBars);
        setLoadedBarsKey(key);
        setBarsError(null);
        setBarsRefreshError(null);
      }
    } catch (error) {
      if (mounted.current && sequence === barsRequestSequence.current) {
        if (isAbortError(error)) return;
        const message = readableError(error, "K 线");
        if (keepsCurrentChart) {
          setBarsRefreshError({ key, message: `刷新失败：${message}` });
        } else {
          setBarsError({ key, message });
        }
      }
    } finally {
      if (mounted.current && sequence === barsRequestSequence.current) {
        setBarsLoadingKey((current) => current === key ? null : current);
        setBarsRefreshingKey((current) => current === key ? null : current);
        if (activeBarsRequestKeyRef.current === key) activeBarsRequestKeyRef.current = null;
      }
    }
  }, [code, market, selectedPeriod]);

  useEffect(() => {
    const controller = new AbortController();
    void loadStock(controller.signal);
    void loadMarketSummary(controller.signal);
    return () => controller.abort();
  }, [loadMarketSummary, loadStock]);

  useEffect(() => {
    const controller = new AbortController();
    void loadMacd(controller.signal);
    return () => controller.abort();
  }, [loadMacd]);

  useEffect(() => {
    const controller = new AbortController();
    void loadBars(controller.signal);
    return () => controller.abort();
  }, [loadBars]);

  const pollToday = useCallback(async (signal: AbortSignal) => {
    const key = selectedBarsKeyRef.current;
    if (!key || activeBarsRequestKeyRef.current === key) return;
    try {
      const summary = await marketApi.getSummary({ signal });
      if (!mounted.current || selectedBarsKeyRef.current !== key) return;
      setMarketSummary(summary);
      setBarsRefreshError((current) => current?.key === key ? null : current);
      if (summary.market_status === "open" && activeBarsRequestKeyRef.current !== key) {
        await Promise.all([loadBars(signal), loadMacd(signal)]);
      }
    } catch (error) {
      if (isAbortError(error)) return;
      if (mounted.current && selectedBarsKeyRef.current === key) {
        setBarsRefreshError({
          key,
          message: `自动刷新状态检查失败：${error instanceof Error ? error.message : "未知错误"}`,
        });
      }
    }
  }, [loadBars, loadMacd]);

  usePolling(pollToday, 60_000, {
    enabled: selectedPeriod.label === "今日",
    immediate: false,
  });

  if (!market || !code) {
    return (
      <section className="stock-detail-page">
        <div className="detail-route-error" role="alert">
          <strong>股票市场参数无效</strong>
          <p>请返回全部股票，并从列表进入有效的沪、深、北市场股票。</p>
          <Link to="/">返回全部股票</Link>
        </div>
      </section>
    );
  }

  const change = movement(stock?.change_percent);
  const changeAmount = movement(stock?.change_amount);
  const changePercent = movement(stock?.change_percent);
  const quality = qualityMeta(stock?.quality_status ?? null);
  const marketStatus = marketSummary?.market_status === "open"
    ? { label: "开市（交易中）", className: "is-open" }
    : marketSummary?.market_status === "closed"
      ? { label: "闭市（已收盘）", className: "is-closed" }
      : { label: "市场状态确认中", className: "is-pending" };
  const visibleBars = loadedBarsKey === selectedBarsKey ? bars : null;
  const matchingBarsError = barsError?.key === selectedBarsKey ? barsError.message : null;
  const matchingRefreshError = barsRefreshError?.key === selectedBarsKey
    ? barsRefreshError.message
    : null;
  const barsLoading = barsLoadingKey === selectedBarsKey
    || (!visibleBars && !matchingBarsError);
  const barsRefreshing = barsRefreshingKey === selectedBarsKey;
  const latestBar = visibleBars?.items.at(-1);
  const matchingMacd = macd?.period === selectedPeriod.period ? macd : null;
  const chartMacd = visibleBars && matchingMacd?.period === visibleBars.period ? matchingMacd : null;
  const macdSummary = matchingMacd?.summary;
  const quoteFields = [
    { label: "最新价", value: formatMarketNumber(stock?.latest_price), className: change.className },
    { label: "涨跌额", value: formatSigned(stock?.change_amount), className: changeAmount.className, direction: changeAmount.label },
    { label: "涨跌幅", value: formatSigned(stock?.change_percent, "%"), className: changePercent.className, direction: changePercent.label },
    { label: "今开", value: formatMarketNumber(stock?.open_price), className: "" },
    { label: "昨收", value: formatMarketNumber(stock?.previous_close), className: "" },
    { label: "最高", value: formatMarketNumber(stock?.high_price), className: "" },
    { label: "最低", value: formatMarketNumber(stock?.low_price), className: "" },
    { label: "成交量（股）", value: formatCompact(stock?.volume), className: "" },
    { label: "成交额", value: formatCompact(stock?.amount), className: "" },
    { label: "换手率", value: isFiniteNumber(stock?.turnover_rate) ? `${formatMarketNumber(stock.turnover_rate)}%` : "—", className: "" },
    { label: "总市值", value: formatCompact(stock?.total_market_cap), className: "" },
  ];

  return (
    <section className="stock-detail-page">
      <header className="detail-header">
        <div>
          <div className="detail-links">
            <Link to="/">← 返回全部股票</Link>
            <StockSwitcher onSelect={(candidate) => {
              setSelectedPeriod(DEFAULT_PERIOD);
              navigate(`/stocks/${candidate.market}/${candidate.code}`);
            }} />
          </div>
          <span className="page-kicker">STOCK / {market}.{code}</span>
          <h1>{stock?.name ?? (stockLoading ? "正在读取股票…" : `${market}.${code}`)}</h1>
          <p className="stock-code data-value">{market}.{code} · {change.label}</p>
          <div className="detail-data-meta" aria-label="股票数据状态">
            <span>股票快照 {formatShanghaiDateTime(stock?.captured_at)}</span>
            <strong className={quality.className}>{quality.label}</strong>
            <strong className={`detail-market-state ${marketStatus.className}`}>
              {marketStatus.label}
            </strong>
            {marketSummary?.stale && <strong className="is-warning">市场摘要已过期</strong>}
          </div>
        </div>
        {stockError && (
          <div className="stock-error" role="alert">
            <span>{stockError}</span>
            <button type="button" onClick={() => void loadStock()}>重新加载股票</button>
          </div>
        )}
      </header>

      <div className="quote-grid" aria-label="核心行情">
        {quoteFields.map((field) => (
          <div className="quote-item" key={field.label}>
            <span>{field.label}</span>
            <strong className={`data-value ${field.className}`}>{field.value}</strong>
            {field.direction && <small>{field.direction}</small>}
          </div>
        ))}
      </div>

      <div className="detail-workspace">
      <section className="kline-panel" aria-label="K 线行情">
        <div className="period-toolbar">
          <div className="period-tabs" aria-label="K 线周期">
            {PERIODS.map((option) => (
              <button
                type="button"
                key={option.label}
                aria-pressed={selectedPeriod.label === option.label}
                onClick={() => {
                  setBarsRefreshError(null);
                  setSelectedPeriod(option);
                }}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="period-caption">
            {selectedPeriod.label === "今日" ? <strong>一分钟一根</strong> : <span>{selectedPeriod.adjustment === "qfq" ? "前复权" : "不复权"}</span>}
            {barsRefreshing && <span role="status">正在刷新 K 线…</span>}
            {matchingRefreshError && <span className="refresh-error" role="alert">{matchingRefreshError}</span>}
          </div>
        </div>

        {visibleBars && (
          <div className="bars-data-meta" aria-label="K 线数据状态">
            <span>K 线来源 {visibleBars.source?.trim() || "未知"}</span>
            <span>最后更新 {formatShanghaiDateTime(visibleBars.last_updated_at)}</span>
          </div>
        )}

        {latestBar && !matchingBarsError && (
          <div className="ohlc-strip" aria-label="最新一根 K 线">
            <span>开 <b>{formatMarketNumber(latestBar.open_price)}</b></span>
            <span>高 <b>{formatMarketNumber(latestBar.high_price)}</b></span>
            <span>低 <b>{formatMarketNumber(latestBar.low_price)}</b></span>
            <span>收 <b>{formatMarketNumber(latestBar.close_price)}</b></span>
            <span>量 <b>{formatCompact(latestBar.volume)} 股</b></span>
            {!latestBar.is_complete && <strong className="dynamic-bar">动态柱</strong>}
          </div>
        )}

        <div className="chart-state">
          {barsLoading && !visibleBars ? (
            <div className="empty-state" role="status">正在加载 K 线数据…</div>
          ) : matchingBarsError ? (
            <div className="chart-error" role="alert">
              <strong>暂时无法读取 K 线</strong>
              <p>{matchingBarsError}</p>
              <button type="button" onClick={() => void loadBars()}>重新加载 K 线</button>
            </div>
          ) : visibleBars && visibleBars.items.length === 0 ? (
            <div className="empty-state">
              <strong>当前周期暂无 K 线数据</strong>
              <span>可尝试切换其他周期或稍后重试。</span>
            </div>
          ) : visibleBars ? (
            <KlineChart series={visibleBars} macd={chartMacd} />
          ) : null}
        </div>
        <footer className="chart-note">
          <span>拖动图表或底部滑块可缩放查看区间</span>
          <span>红色为上涨，绿色为下跌；当前周期下方可查看 MACD 副图</span>
        </footer>
      </section>
      <aside className="indicator-panel" aria-label="技术指标结果">
        <article className="indicator-card">
          <header>
            <div>
              <span className="page-kicker">INDICATOR / {indicatorPeriodLabel(selectedPeriod)}</span>
              <h2>MACD 指标 · {indicatorPeriodLabel(selectedPeriod)}</h2>
            </div>
            <strong className={`indicator-quality ${macdSummary?.quality ?? "pending"}`}>
              {macdQualityLabel(macdSummary?.quality)}
            </strong>
          </header>

          {macdLoading && !matchingMacd ? (
            <div className="indicator-state" role="status">正在计算 MACD…</div>
          ) : macdError ? (
            <div className="indicator-state is-error" role="alert">{macdError}</div>
          ) : macdSummary ? (
            <>
              <strong className={`indicator-main-signal ${macdSignalTone(macdSummary.signal_type)}`}>
                {macdSummary.recent_signal_label}
              </strong>
              <dl className="indicator-metrics">
                <div>
                  <dt>零轴</dt>
                  <dd>{zeroAxisLabel(macdSummary.zero_axis)}</dd>
                </div>
                <div>
                  <dt>DIFF</dt>
                  <dd className="data-value">DIFF {formatIndicatorValue(macdSummary.diff)}</dd>
                </div>
                <div>
                  <dt>DEA</dt>
                  <dd className="data-value">DEA {formatIndicatorValue(macdSummary.dea)}</dd>
                </div>
                <div>
                  <dt>红绿柱</dt>
                  <dd className="data-value">{formatIndicatorValue(macdSummary.histogram)}</dd>
                </div>
              </dl>
              <p>
                信号日期 {macdSummary.signal_date ?? "暂无"} · 计算时间{" "}
                {formatShanghaiDateTime(macdSummary.calculated_at)}
              </p>
              <small>
                {macdSummary.is_intraday
                  ? "当前含本周期动态柱，周期收束后会随 K 线固化。"
                  : `基于最近${indicatorPeriodLabel(selectedPeriod)} K 线计算。`}
              </small>
            </>
          ) : (
            <div className="indicator-state">暂无 MACD 指标</div>
          )}
        </article>
      </aside>
      </div>
    </section>
  );
}
