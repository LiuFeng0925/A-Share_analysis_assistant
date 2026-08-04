import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { marketApi } from "../api/client";
import type {
  Adjustment,
  BarPeriod,
  BarRange,
  BarSeries,
  Market,
  StockQuote,
} from "../api/types";
import { KlineChart } from "../components/KlineChart";

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

function formatNumber(value: number | null, digits = 2) {
  return value === null ? "—" : value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatCompact(value: number | null) {
  if (value === null) return "—";
  if (Math.abs(value) >= 100_000_000) return `${formatNumber(value / 100_000_000)} 亿`;
  if (Math.abs(value) >= 10_000) return `${formatNumber(value / 10_000)} 万`;
  return formatNumber(value, 0);
}

function movement(value: number | null) {
  if (value === null || value === 0) return { className: "", label: "平盘", prefix: "" };
  return value > 0
    ? { className: "up", label: "上涨", prefix: "+" }
    : { className: "down", label: "下跌", prefix: "" };
}

function readableError(error: unknown, scope: "股票" | "K 线") {
  const message = error instanceof Error ? error.message : "未知错误";
  return `${scope}加载失败：${message}`;
}

export function isAShareTradingTime(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  if (value.weekday === "Sat" || value.weekday === "Sun") return false;
  const minute = Number(value.hour) * 60 + Number(value.minute);
  return (minute >= 570 && minute <= 690) || (minute >= 780 && minute <= 900);
}

export function StockDetailPage() {
  const params = useParams();
  const market = useMemo(() => asMarket(params.market), [params.market]);
  const code = params.code?.trim() ?? "";
  const [selectedPeriod, setSelectedPeriod] = useState<(typeof PERIODS)[number]>(DEFAULT_PERIOD);
  const [stock, setStock] = useState<StockQuote | null>(null);
  const [bars, setBars] = useState<BarSeries | null>(null);
  const [stockLoading, setStockLoading] = useState(Boolean(market && code));
  const [barsLoading, setBarsLoading] = useState(Boolean(market && code));
  const [stockError, setStockError] = useState<string | null>(null);
  const [barsError, setBarsError] = useState<string | null>(null);
  const stockRequestSequence = useRef(0);
  const barsRequestSequence = useRef(0);
  const mounted = useRef(true);

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
    const sequence = ++barsRequestSequence.current;
    setBarsLoading(true);
    setBarsError(null);
    setBars(null);
    try {
      const nextBars = await marketApi.getBars({
        market,
        code,
        period: selectedPeriod.period,
        range: selectedPeriod.range,
        adjustment: selectedPeriod.adjustment,
      });
      if (mounted.current && sequence === barsRequestSequence.current) setBars(nextBars);
    } catch (error) {
      if (mounted.current && sequence === barsRequestSequence.current) {
        setBars(null);
        setBarsError(readableError(error, "K 线"));
      }
    } finally {
      if (mounted.current && sequence === barsRequestSequence.current) setBarsLoading(false);
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
    const timer = window.setInterval(() => {
      if (isAShareTradingTime()) void loadBars();
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [loadBars, selectedPeriod.label]);

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

  const change = movement(stock?.change_percent ?? null);
  const latestBar = bars?.items.at(-1);
  const quoteFields = [
    ["最新价", formatNumber(stock?.latest_price ?? null)],
    ["涨跌额", stock?.change_amount == null ? "—" : `${change.prefix}${formatNumber(stock.change_amount)}`],
    ["涨跌幅", stock?.change_percent == null ? "—" : `${change.prefix}${formatNumber(stock.change_percent)}%`],
    ["今开", formatNumber(stock?.open_price ?? null)],
    ["最高", formatNumber(stock?.high_price ?? null)],
    ["最低", formatNumber(stock?.low_price ?? null)],
    ["成交量", formatCompact(stock?.volume ?? null)],
    ["成交额", formatCompact(stock?.amount ?? null)],
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
        </div>
        {stockError && (
          <div className="stock-error" role="alert">
            <span>{stockError}</span>
            <button type="button" onClick={() => void loadStock()}>重新加载股票</button>
          </div>
        )}
      </header>

      <div className="quote-grid" aria-label="核心行情">
        {quoteFields.map(([label, value], index) => (
          <div className="quote-item" key={label}>
            <span>{label}</span>
            <strong className={`data-value ${index < 3 ? change.className : ""}`}>{value}</strong>
            {index === 2 && <small>{change.label}</small>}
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
                onClick={() => setSelectedPeriod(option)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="period-caption">
            {selectedPeriod.label === "今日" ? <strong>一分钟一根</strong> : <span>{selectedPeriod.adjustment === "qfq" ? "前复权" : "不复权"}</span>}
            {barsLoading && <span role="status">正在更新…</span>}
          </div>
        </div>

        {latestBar && !barsError && !barsLoading && (
          <div className="ohlc-strip" aria-label="最新一根 K 线">
            <span>开 <b>{formatNumber(latestBar.open_price)}</b></span>
            <span>高 <b>{formatNumber(latestBar.high_price)}</b></span>
            <span>低 <b>{formatNumber(latestBar.low_price)}</b></span>
            <span>收 <b>{formatNumber(latestBar.close_price)}</b></span>
            <span>量 <b>{formatCompact(latestBar.volume)}</b></span>
          </div>
        )}

        <div className="chart-state">
          {barsLoading ? (
            <div className="empty-state" role="status">正在加载 K 线数据…</div>
          ) : barsError ? (
            <div className="chart-error" role="alert">
              <strong>暂时无法读取 K 线</strong>
              <p>{barsError}</p>
              <button type="button" onClick={() => void loadBars()}>重新加载 K 线</button>
            </div>
          ) : bars && bars.items.length === 0 ? (
            <div className="empty-state">
              <strong>当前周期暂无 K 线数据</strong>
              <span>可尝试切换其他周期或稍后重试。</span>
            </div>
          ) : bars ? (
            <KlineChart series={bars} />
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
