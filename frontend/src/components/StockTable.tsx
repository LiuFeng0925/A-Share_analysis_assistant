import { Link, useNavigate } from "react-router-dom";
import type { StockQuery, StockQuote } from "../api/types";

interface StockTableProps {
  stocks: StockQuote[];
  sortBy: StockQuery["sortBy"];
  sortOrder: StockQuery["sortOrder"];
  onSort: (field: StockQuery["sortBy"]) => void;
}

const numberFormatter = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });

function formatNumber(value: number | null, digits = 2) {
  if (value === null || !Number.isFinite(value)) return "--";
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatCompact(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "--";
  if (Math.abs(value) >= 1_000_000_000_000) {
    return `${numberFormatter.format(value / 1_000_000_000_000)} 万亿`;
  }
  if (Math.abs(value) >= 100_000_000) return `${numberFormatter.format(value / 100_000_000)} 亿`;
  if (Math.abs(value) >= 10_000) return `${numberFormatter.format(value / 10_000)} 万`;
  return numberFormatter.format(value);
}

function signed(value: number | null, suffix = "") {
  if (value === null || !Number.isFinite(value)) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value)}${suffix}`;
}

function tone(value: number | null) {
  if (value === null || !Number.isFinite(value) || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

function formatRange(low: number | null, high: number | null) {
  if (low === null || high === null || !Number.isFinite(low) || !Number.isFinite(high)) {
    return "--";
  }
  return `${formatNumber(low)} — ${formatNumber(high)}`;
}

function formatCapturedAt(value: string | null) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(date).replaceAll("/", "-");
}

function macdSignalClass(signal: StockQuote["macd_signal_type"]) {
  if (signal === "golden_cross") return "is-golden";
  if (signal === "death_cross") return "is-death";
  return "is-empty";
}

function formatMacdSignal(stock: StockQuote) {
  if (stock.macd_signal_label) return stock.macd_signal_label;
  if (stock.macd_quality === "insufficient") return "数据不足";
  return "--";
}

function SortButton({
  field,
  label,
  sortBy,
  sortOrder,
  onSort,
}: {
  field: StockQuery["sortBy"];
  label: string;
  sortBy: StockQuery["sortBy"];
  sortOrder: StockQuery["sortOrder"];
  onSort: (field: StockQuery["sortBy"]) => void;
}) {
  const active = sortBy === field;
  return (
    <button
      className={active ? "table-sort is-active" : "table-sort"}
      type="button"
      aria-label={`按${label}排序`}
      aria-pressed={active}
      onClick={() => onSort(field)}
    >
      {label}
      <span aria-hidden="true">{active ? (sortOrder === "asc" ? "↑" : "↓") : "↕"}</span>
    </button>
  );
}

export function StockTable({ stocks, sortBy, sortOrder, onSort }: StockTableProps) {
  const navigate = useNavigate();
  const ariaSort = (field: StockQuery["sortBy"]) => {
    if (sortBy !== field) return "none" as const;
    return sortOrder === "asc" ? "ascending" as const : "descending" as const;
  };

  return (
    <div className="stock-table-scroll">
      <table className="stock-table">
        <thead>
          <tr>
            <th scope="col" aria-sort={ariaSort("code")}>
              <SortButton field="code" label="股票" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col">日 K MACD</th>
            <th scope="col" aria-sort={ariaSort("latest_price")}>
              <SortButton field="latest_price" label="最新价" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col" aria-sort={ariaSort("change_percent")}>
              <SortButton field="change_percent" label="涨跌幅" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col">涨跌额</th>
            <th scope="col">今开</th>
            <th scope="col">今日区间</th>
            <th scope="col">成交量（股）</th>
            <th scope="col" aria-sort={ariaSort("amount")}>
              <SortButton field="amount" label="成交额" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col" aria-sort={ariaSort("turnover_rate")}>
              <SortButton field="turnover_rate" label="换手率" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col" aria-sort={ariaSort("total_market_cap")}>
              <SortButton field="total_market_cap" label="总市值" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col">更新时间</th>
          </tr>
        </thead>
        <tbody>
          {stocks.map((stock) => {
            const changeTone = tone(stock.change_percent);
            const scaleWidth = stock.change_percent === null || !Number.isFinite(stock.change_percent)
              ? 0
              : Math.min(42, Math.max(8, Math.abs(stock.change_percent) * 7));
            const detailPath = `/stocks/${stock.market}/${stock.code}`;
            return (
              <tr
                key={`${stock.market}-${stock.code}`}
                tabIndex={0}
                aria-label={`${stock.name} ${stock.code}`}
                onClick={() => navigate(detailPath)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && event.target === event.currentTarget) {
                    navigate(detailPath);
                  }
                }}
              >
                <td>
                  <div className="stock-identity">
                    <span className="market-badge">{stock.market}</span>
                    <span>
                      <Link
                        to={detailPath}
                        onClick={(event) => event.stopPropagation()}
                      >
                        {stock.name}
                      </Link>
                      <small className="data-value">{stock.code}</small>
                    </span>
                  </div>
                </td>
                <td>
                  <span className={`macd-signal ${macdSignalClass(stock.macd_signal_type)}`}>
                    {formatMacdSignal(stock)}
                  </span>
                </td>
                <td className={`data-value price-cell ${changeTone}`}>
                  {formatNumber(stock.latest_price)}
                </td>
                <td className={`data-value change-cell ${changeTone}`}>
                  <span>{signed(stock.change_percent, "%")}</span>
                  <i
                    className="spread-scale"
                    aria-hidden="true"
                    style={{ width: `${scaleWidth}px` }}
                  />
                </td>
                <td className={`data-value ${tone(stock.change_amount)}`}>
                  {signed(stock.change_amount)}
                </td>
                <td className="data-value">{formatNumber(stock.open_price)}</td>
                <td className="data-value range-cell">{formatRange(stock.low_price, stock.high_price)}</td>
                <td className="data-value">{formatCompact(stock.volume)}</td>
                <td className="data-value">{formatCompact(stock.amount)}</td>
                <td className="data-value">
                  {stock.turnover_rate === null || !Number.isFinite(stock.turnover_rate)
                    ? "--"
                    : `${formatNumber(stock.turnover_rate)}%`}
                </td>
                <td className="data-value">{formatCompact(stock.total_market_cap)}</td>
                <td className="data-value update-cell">{formatCapturedAt(stock.captured_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
