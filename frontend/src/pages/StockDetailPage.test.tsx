import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";
import { marketApi } from "../api/client";
import { dailyBarsFixture, stockDetailFixture, todayBarsFixture } from "../test/fixtures";
import { isAShareTradingTime, StockDetailPage } from "./StockDetailPage";

vi.mock("../api/client", () => ({
  marketApi: { getStock: vi.fn(), getBars: vi.fn() },
}));

vi.mock("../components/KlineChart", () => ({
  KlineChart: ({ series }: { series: typeof dailyBarsFixture }) => (
    <div data-testid="kline-chart">{series.period}:{series.items.length}</div>
  ),
}));

beforeEach(() => {
  vi.mocked(marketApi.getStock).mockReset().mockResolvedValue(stockDetailFixture);
  vi.mocked(marketApi.getBars).mockReset().mockResolvedValue(dailyBarsFixture);
});

function renderDetail(path = "/stocks/SH/600519") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/stocks/:market/:code" element={<StockDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("默认请求最近 60 个交易日的前复权日 K", async () => {
  renderDetail();

  expect(await screen.findByRole("heading", { name: /贵州茅台/ })).toBeInTheDocument();
  expect(marketApi.getStock).toHaveBeenCalledWith("SH", "600519");
  expect(marketApi.getBars).toHaveBeenCalledWith({
    market: "SH",
    code: "600519",
    period: "1d",
    range: "60d",
    adjustment: "qfq",
  });
  expect(screen.getByRole("button", { name: "日K" })).toHaveAttribute("aria-pressed", "true");
});

test("今日按钮请求当天原生一分钟 K 并解释颗粒度", async () => {
  vi.mocked(marketApi.getBars)
    .mockResolvedValueOnce(dailyBarsFixture)
    .mockResolvedValueOnce(todayBarsFixture);
  renderDetail();

  fireEvent.click(await screen.findByRole("button", { name: "今日" }));
  await waitFor(() =>
    expect(marketApi.getBars).toHaveBeenLastCalledWith(
      expect.objectContaining({ period: "1m", range: "today", adjustment: "none" }),
    ),
  );
  expect(screen.getByText("一分钟一根")).toBeInTheDocument();
});

test("较早周期响应不会覆盖较新的周期数据", async () => {
  let resolveDaily: ((value: typeof dailyBarsFixture) => void) | undefined;
  vi.mocked(marketApi.getBars)
    .mockReturnValueOnce(new Promise((resolve) => { resolveDaily = resolve; }))
    .mockResolvedValueOnce(todayBarsFixture);
  renderDetail();

  fireEvent.click(screen.getByRole("button", { name: "今日" }));
  expect(await screen.findByTestId("kline-chart")).toHaveTextContent("1m:1");
  resolveDaily?.(dailyBarsFixture);
  await Promise.resolve();
  expect(screen.getByTestId("kline-chart")).toHaveTextContent("1m:1");
});

test("非法市场参数显示中文错误且不发送行情请求", async () => {
  renderDetail("/stocks/XX/600519");

  expect(await screen.findByRole("alert")).toHaveTextContent("股票市场参数无效");
  expect(marketApi.getStock).not.toHaveBeenCalled();
  expect(marketApi.getBars).not.toHaveBeenCalled();
});

test("接口失败显示互斥中文错误并可重试", async () => {
  vi.mocked(marketApi.getBars)
    .mockRejectedValueOnce(new Error("网络错误"))
    .mockResolvedValueOnce(dailyBarsFixture);
  renderDetail();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("K 线加载失败");
  expect(screen.queryByTestId("kline-chart")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重新加载 K 线" }));
  expect(await screen.findByTestId("kline-chart")).toBeInTheDocument();
});

test("空 K 线只显示空状态而不渲染图表", async () => {
  vi.mocked(marketApi.getBars).mockResolvedValue({ ...dailyBarsFixture, items: [] });
  renderDetail();

  expect(await screen.findByText("当前周期暂无 K 线数据")).toBeInTheDocument();
  expect(screen.queryByTestId("kline-chart")).not.toBeInTheDocument();
});

test("今日刷新只把工作日开盘区间视为交易时段", () => {
  expect(isAShareTradingTime(new Date("2026-08-04T02:00:00Z"))).toBe(true);
  expect(isAShareTradingTime(new Date("2026-08-04T04:00:00Z"))).toBe(false);
  expect(isAShareTradingTime(new Date("2026-08-04T06:00:00Z"))).toBe(true);
  expect(isAShareTradingTime(new Date("2026-08-08T02:00:00Z"))).toBe(false);
});
