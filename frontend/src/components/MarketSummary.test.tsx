import { render, screen } from "@testing-library/react";
import { summaryFixture } from "../test/fixtures";
import { MarketSummary } from "./MarketSummary";

test("成交额在亿元与万亿元边界使用正确单位", () => {
  const { rerender } = render(
    <MarketSummary summary={{ ...summaryFixture, amount: 100_000_000_000 }} />,
  );
  expect(screen.getByText("1,000 亿")).toBeInTheDocument();

  rerender(<MarketSummary summary={{ ...summaryFixture, amount: 1_000_000_000_000 }} />);
  expect(screen.getByText("1.00 万亿")).toBeInTheDocument();
});

test("概览中的非有限数值统一显示占位符", () => {
  render(
    <MarketSummary
      summary={{
        ...summaryFixture,
        total: Number.NaN,
        rising: Number.POSITIVE_INFINITY,
        amount: Number.NEGATIVE_INFINITY,
      }}
    />,
  );

  expect(screen.getAllByText("--")).toHaveLength(3);
  expect(screen.queryByText(/NaN|Infinity/)).not.toBeInTheDocument();
});
