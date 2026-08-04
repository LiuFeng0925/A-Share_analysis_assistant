import { useNavigate } from "react-router-dom";
import type { StockQuery, StockQuote } from "../api/types";

interface StockTableProps {
  stocks: StockQuote[];
  sortBy: StockQuery["sortBy"];
  sortOrder: StockQuery["sortOrder"];
  onSort: (field: StockQuery["sortBy"]) => void;
}

const numberFormatter = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });

function formatNumber(value: number | null, digits = 2) {
  if (value === null) return "--";
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatCompact(value: number | null) {
  if (value === null) return "--";
  if (value >= 100_000_000_000) return `${numberFormatter.format(value / 100_000_000_000)} 万亿`;
  if (value >= 100_000_000) return `${numberFormatter.format(value / 100_000_000)} 亿`;
  if (value >= 10_000) return `${numberFormatter.format(value / 10_000)} 万`;
  return numberFormatter.format(value);
}

function signed(value: number | null, suffix = "") {
  if (value === null) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value)}${suffix}`;
}

function tone(value: number | null) {
  if (value === null || value === 0) return "flat";
  return value > 0 ? "up" : "down";
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

  return (
    <div className="stock-table-scroll">
      <table className="stock-table">
        <thead>
          <tr>
            <th scope="col">
              <SortButton field="code" label="股票" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col">
              <SortButton field="latest_price" label="最新价" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col">
              <SortButton field="change_percent" label="涨跌幅" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col">涨跌额</th>
            <th scope="col">今开</th>
            <th scope="col">最高</th>
            <th scope="col">最低</th>
            <th scope="col">
              <SortButton field="amount" label="成交额" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col">
              <SortButton field="turnover_rate" label="换手率" {...{ sortBy, sortOrder, onSort }} />
            </th>
            <th scope="col">
              <SortButton field="total_market_cap" label="总市值" {...{ sortBy, sortOrder, onSort }} />
            </th>
          </tr>
        </thead>
        <tbody>
          {stocks.map((stock) => {
            const changeTone = tone(stock.change_percent);
            const scaleWidth = stock.change_percent === null
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
                  if (event.key === "Enter") navigate(detailPath);
                }}
              >
                <td>
                  <div className="stock-identity">
                    <span className="market-badge">{stock.market}</span>
                    <span>
                      <strong>{stock.name}</strong>
                      <small className="data-value">{stock.code}</small>
                    </span>
                  </div>
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
                <td className="data-value">{formatNumber(stock.high_price)}</td>
                <td className="data-value">{formatNumber(stock.low_price)}</td>
                <td className="data-value">{formatCompact(stock.amount)}</td>
                <td className="data-value">
                  {stock.turnover_rate === null ? "--" : `${formatNumber(stock.turnover_rate)}%`}
                </td>
                <td className="data-value">{formatCompact(stock.total_market_cap)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
