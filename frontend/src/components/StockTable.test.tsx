import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { stockPageFixture } from "../test/fixtures";
import { StockTable } from "./StockTable";

const stock = stockPageFixture.items[0];

test("一行展示今日区间、更新时间，并按亿元和万亿元格式化关键字段", () => {
  render(
    <MemoryRouter>
      <StockTable
        stocks={[{
          ...stock,
          volume: 100_000_000,
          amount: 100_000_000_000,
          total_market_cap: 1_000_000_000_000,
        }]}
        sortBy="code"
        sortOrder="asc"
        onSort={vi.fn()}
      />
    </MemoryRouter>,
  );

  expect(screen.getAllByRole("columnheader").map((header) => header.textContent?.replace(/[↑↓↕]/g, "")))
    .toEqual([
      "股票",
      "日 K MACD",
      "最新价",
      "涨跌幅",
      "涨跌额",
      "今开",
      "今日区间",
      "成交量（股）",
      "成交额",
      "换手率",
      "总市值",
      "更新时间",
    ]);
  expect(screen.getByRole("columnheader", { name: "成交量（股）" })).toBeInTheDocument();
  expect(screen.getByText("近 3 日金叉")).toBeInTheDocument();
  expect(screen.getByText("1,551.01 — 1,599.90")).toBeInTheDocument();
  expect(screen.getByText("08-04 10:26:00")).toBeInTheDocument();
  expect(screen.getByText("1 亿")).toBeInTheDocument();
  expect(screen.getByText("1,000 亿")).toBeInTheDocument();
  expect(screen.getByText("1 万亿")).toBeInTheDocument();
});

test("列头暴露排序语义且股票名称是真实详情链接", () => {
  const onSort = vi.fn();
  const { rerender } = render(
    <MemoryRouter>
      <StockTable stocks={[stock]} sortBy="code" sortOrder="asc" onSort={onSort} />
    </MemoryRouter>,
  );

  expect(screen.getByRole("columnheader", { name: /股票/ })).toHaveAttribute("aria-sort", "ascending");
  expect(screen.getByRole("columnheader", { name: /涨跌幅/ })).toHaveAttribute("aria-sort", "none");
  expect(screen.getByRole("link", { name: "贵州茅台" })).toHaveAttribute(
    "href",
    "/stocks/SH/600519",
  );

  fireEvent.click(screen.getByRole("button", { name: "按涨跌幅排序" }));
  expect(onSort).toHaveBeenCalledWith("change_percent");

  rerender(
    <MemoryRouter>
      <StockTable stocks={[stock]} sortBy="change_percent" sortOrder="desc" onSort={onSort} />
    </MemoryRouter>,
  );
  expect(screen.getByRole("columnheader", { name: /涨跌幅/ })).toHaveAttribute("aria-sort", "descending");
});

test("非有限行情值不会泄漏到数字或价差标尺", () => {
  const invalidStock = {
    ...stock,
    latest_price: Number.NaN,
    change_percent: Number.POSITIVE_INFINITY,
    change_amount: Number.NEGATIVE_INFINITY,
    volume: Number.NaN,
    amount: Number.POSITIVE_INFINITY,
    total_market_cap: Number.NEGATIVE_INFINITY,
  };
  const { container } = render(
    <MemoryRouter>
      <StockTable stocks={[invalidStock]} sortBy="code" sortOrder="asc" onSort={vi.fn()} />
    </MemoryRouter>,
  );

  expect(container).not.toHaveTextContent(/NaN|Infinity/);
  expect(container.querySelector(".spread-scale")).toHaveStyle({ width: "0px" });
});
