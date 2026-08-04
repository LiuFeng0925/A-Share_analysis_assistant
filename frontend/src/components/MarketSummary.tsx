import type { MarketSummary as MarketSummaryData } from "../api/types";

interface MarketSummaryProps {
  summary: MarketSummaryData | null;
}

const integerFormatter = new Intl.NumberFormat("zh-CN");

function formatAmount(value: number) {
  if (value >= 100_000_000_000) return `${(value / 100_000_000_000).toFixed(2)} 万亿`;
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(0)} 亿`;
  return integerFormatter.format(value);
}

export function MarketSummary({ summary }: MarketSummaryProps) {
  const items = [
    { label: "全部股票", value: summary ? integerFormatter.format(summary.total) : "--", tone: "" },
    { label: "上涨", value: summary ? integerFormatter.format(summary.rising) : "--", tone: "up" },
    { label: "下跌", value: summary ? integerFormatter.format(summary.falling) : "--", tone: "down" },
    { label: "平盘", value: summary ? integerFormatter.format(summary.flat) : "--", tone: "" },
    { label: "成交额", value: summary ? formatAmount(summary.amount) : "--", tone: "" },
  ];

  return (
    <section className="summary-grid" aria-label="市场概览">
      {items.map((item) => (
        <article className="summary-card" key={item.label}>
          <span>{item.label}</span>
          <strong className={`data-value ${item.tone}`}>{item.value}</strong>
        </article>
      ))}
    </section>
  );
}
