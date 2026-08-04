import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { marketApi } from "../api/client";
import type {
  Adjustment,
  BarPeriod,
  BarRange,
  BarSeries,
  Market,
  QualityStatus,
  StockQuote,
} from "../api/types";
import { KlineChart } from "../components/KlineChart";
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

function periodKey(market: Market, code: string, option: PeriodOption) {
  return `${market}/${code}/${option.period}/${option.range}/${option.adjustment}`;
}

function readableError(error: unknown, scope: "股票" | "K 线") {
  const message = error instanceof Error ? error.message : "未知错误";
  return `${scope}加载失败：${message}`;
}

export function StockDetailPage() {
  const params = useParams();
  const market = useMemo(() => asMarket(params.market), [params.market]);
  const code = params.code?.trim() ?? "";
  const [selectedPeriod, setSelectedPeriod] = useState<(typeof PERIODS)[number]>(DEFAULT_PERIOD);
  const [stock, setStock] = useState<StockQuote | null>(null);
  const [bars, setBars] = useState<BarSeries | null>(null);
  const [loadedBarsKey, setLoadedBarsKey] = useState<string | null>(null);
  const [stockLoading, setStockLoading] = useState(Boolean(market && code));
  const [barsLoadingKey, setBarsLoadingKey] = useState<string | null>(null);
  const [barsRefreshingKey, setBarsRefreshingKey] = useState<string | null>(null);
  const [stockError, setStockError] = useState<string | null>(null);
  const [barsError, setBarsError] = useState<{ key: string; message: string } | null>(null);
  const [barsRefreshError, setBarsRefreshError] = useState<{ key: string; message: string } | null>(null);
  const stockRequestSequence = useRef(0);
  const barsRequestSequence = useRef(0);
  const barsRef = useRef<BarSeries | null>(null);
  const loadedBarsKeyRef = useRef<string | null>(null);
  const activeBarsRequestKeyRef = useRef<string | null>(null);
  const selectedBarsKeyRef = useRef<string | null>(null);
  const pollInFlightRef = useRef(false);
  const mounted = useRef(true);
  const selectedBarsKey = market && code ? periodKey(market, code, selectedPeriod) : null;
  selectedBarsKeyRef.current = selectedBarsKey;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      stockRequestSequence.current += 1;
      barsRequestSequence.current += 1;
    };
  }, []);

  const loadStock = useCallback(async () => {
    if (!market || !code) return;
    const sequence = ++stockRequestSequence.current;
    setStockLoading(true);
    setStockError(null);
    setStock(null);
    try {
      const nextStock = await marketApi.getStock(market, code);
      if (mounted.current && sequence === stockRequestSequence.current) setStock(nextStock);
    } catch (error) {
      if (mounted.current && sequence === stockRequestSequence.current) {
        setStock(null);
        setStockError(readableError(error, "股票"));
      }
    } finally {
      if (mounted.current && sequence === stockRequestSequence.current) setStockLoading(false);
    }
  }, [code, market]);

  const loadBars = useCallback(async () => {
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
      });
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
    void loadStock();
  }, [loadStock]);

  useEffect(() => {
    void loadBars();
  }, [loadBars]);

  useEffect(() => {
    if (selectedPeriod.label !== "今日") return;
    const key = selectedBarsKey;
    const timer = window.setInterval(async () => {
      if (!key || pollInFlightRef.current || activeBarsRequestKeyRef.current === key) return;
      pollInFlightRef.current = true;
      try {
        const summary = await marketApi.getSummary();
        if (mounted.current && selectedBarsKeyRef.current === key) {
          setBarsRefreshError((current) => current?.key === key ? null : current);
        }
        if (
          mounted.current
          && selectedBarsKeyRef.current === key
          && summary.market_status === "open"
          && activeBarsRequestKeyRef.current !== key
        ) {
          await loadBars();
        }
      } catch (error) {
        if (mounted.current && selectedBarsKeyRef.current === key) {
          setBarsRefreshError({
            key,
            message: `自动刷新状态检查失败：${error instanceof Error ? error.message : "未知错误"}`,
          });
        }
      } finally {
        pollInFlightRef.current = false;
      }
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [loadBars, selectedBarsKey, selectedPeriod.label]);

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
  const visibleBars = loadedBarsKey === selectedBarsKey ? bars : null;
  const matchingBarsError = barsError?.key === selectedBarsKey ? barsError.message : null;
  const matchingRefreshError = barsRefreshError?.key === selectedBarsKey
    ? barsRefreshError.message
    : null;
  const barsLoading = barsLoadingKey === selectedBarsKey
    || (!visibleBars && !matchingBarsError);
  const barsRefreshing = barsRefreshingKey === selectedBarsKey;
  const latestBar = visibleBars?.items.at(-1);
  const quoteFields = [
    { label: "最新价", value: formatMarketNumber(stock?.latest_price), className: change.className },
    { label: "涨跌额", value: formatSigned(stock?.change_amount), className: changeAmount.className, direction: changeAmount.label },
    { label: "涨跌幅", value: formatSigned(stock?.change_percent, "%"), className: changePercent.className, direction: changePercent.label },
    { label: "今开", value: formatMarketNumber(stock?.open_price), className: "" },
    { label: "最高", value: formatMarketNumber(stock?.high_price), className: "" },
    { label: "最低", value: formatMarketNumber(stock?.low_price), className: "" },
    { label: "成交量（股）", value: formatCompact(stock?.volume), className: "" },
    { label: "成交额", value: formatCompact(stock?.amount), className: "" },
  ];

  return (
    <section className="stock-detail-page">
      <header className="detail-header">
        <div>
          <div className="detail-links">
            <Link to="/">← 返回全部股票</Link>
            <Link to="/">搜索其他股票</Link>
          </div>
          <span className="page-kicker">STOCK / {market}.{code}</span>
          <h1>{stock?.name ?? (stockLoading ? "正在读取股票…" : `${market}.${code}`)}</h1>
          <p className="stock-code data-value">{market}.{code} · {change.label}</p>
          <div className="detail-data-meta" aria-label="股票数据状态">
            <span>股票快照 {formatShanghaiDateTime(stock?.captured_at)}</span>
            <strong className={quality.className}>{quality.label}</strong>
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
            <KlineChart series={visibleBars} />
          ) : null}
        </div>
        <footer className="chart-note">
          <span>拖动图表或底部滑块可缩放查看区间</span>
          <span>红色为上涨，绿色为下跌；同时以开收数值判断方向</span>
        </footer>
      </section>
    </section>
  );
}
