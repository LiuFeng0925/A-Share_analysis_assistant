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

test("普通交叉与去重背离摘要在日K MACD单元格中独立展示", () => {
  render(
    <MemoryRouter>
      <StockTable
        stocks={[{
          ...stock,
          macd_divergence_labels: [
            "bottom_forming",
            "bottom_forming",
            "top_confirmed",
          ],
        }]}
        sortBy="code"
        sortOrder="asc"
        onSort={vi.fn()}
      />
    </MemoryRouter>,
  );

  expect(screen.getByText("近 3 日金叉")).toHaveClass("macd-signal", "is-golden");
  expect(screen.getAllByText("底背离形成中")).toHaveLength(1);
  expect(screen.getByText("底背离形成中")).toHaveClass(
    "macd-divergence-summary",
    "is-bullish-forming",
  );
  expect(screen.getByText("顶背离已确认")).toHaveClass(
    "macd-divergence-summary",
    "is-bearish-confirmed",
  );
});

test("没有普通交叉时仍保留普通标签并展示背离摘要", () => {
  render(
    <MemoryRouter>
      <StockTable
        stocks={[{
          ...stock,
          macd_signal_type: "none",
          macd_signal_label: null,
          macd_divergence_labels: ["bottom_confirmed", "top_forming"],
        }]}
        sortBy="code"
        sortOrder="asc"
        onSort={vi.fn()}
      />
    </MemoryRouter>,
  );

  expect(screen.getByText("--")).toHaveClass("macd-signal", "is-empty");
  expect(screen.getByText("底背离已确认")).toBeInTheDocument();
  expect(screen.getByText("顶背离形成中")).toBeInTheDocument();
});

test("运行时收到未知背离摘要标签时跳过异常值且表格不崩溃", () => {
  expect(() => render(
    <MemoryRouter>
      <StockTable
        stocks={[{
          ...stock,
          macd_divergence_labels: [
            "unknown" as never,
            "bottom_forming",
          ],
        }]}
        sortBy="code"
        sortOrder="asc"
        onSort={vi.fn()}
      />
    </MemoryRouter>,
  )).not.toThrow();

  expect(screen.getByText("近 3 日金叉")).toBeInTheDocument();
  expect(screen.getByText("底背离形成中")).toBeInTheDocument();
  expect(screen.queryByText("unknown")).not.toBeInTheDocument();
});
