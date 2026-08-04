import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { marketApi } from "../api/client";
import { dailyBarsFixture, stockDetailFixture, summaryFixture, todayBarsFixture } from "../test/fixtures";
import { StockDetailPage } from "./StockDetailPage";

vi.mock("../api/client", () => ({
  marketApi: { getStock: vi.fn(), getBars: vi.fn(), getSummary: vi.fn() },
}));

vi.mock("../components/KlineChart", () => ({
  KlineChart: ({ series }: { series: typeof dailyBarsFixture }) => (
    <div data-testid="kline-chart">{series.period}:{series.items.length}</div>
  ),
}));

beforeEach(() => {
  vi.mocked(marketApi.getStock).mockReset().mockResolvedValue(stockDetailFixture);
  vi.mocked(marketApi.getBars).mockReset().mockResolvedValue(dailyBarsFixture);
  vi.mocked(marketApi.getSummary).mockReset().mockResolvedValue(summaryFixture);
});

afterEach(() => {
  vi.useRealTimers();
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
  await act(async () => resolveDaily?.(dailyBarsFixture));
  await waitFor(() => expect(screen.getByTestId("kline-chart")).toHaveTextContent("1m:1"));
});

test("换周期时不把旧图表误标为新周期", async () => {
  let resolveToday: ((value: typeof todayBarsFixture) => void) | undefined;
  vi.mocked(marketApi.getBars)
    .mockResolvedValueOnce(dailyBarsFixture)
    .mockReturnValueOnce(new Promise((resolve) => { resolveToday = resolve; }));
  renderDetail();
  expect(await screen.findByTestId("kline-chart")).toHaveTextContent("1d:1");

  fireEvent.click(screen.getByRole("button", { name: "今日" }));
  expect(screen.queryByTestId("kline-chart")).not.toBeInTheDocument();
  expect(screen.getByText("正在加载 K 线数据…")).toBeInTheDocument();
  await act(async () => resolveToday?.(todayBarsFixture));
  expect(await screen.findByTestId("kline-chart")).toHaveTextContent("1m:1");
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

test("同周期后台刷新保留图表，失败后显示非遮挡提示", async () => {
  vi.useFakeTimers();
  let rejectRefresh: ((error: Error) => void) | undefined;
  vi.mocked(marketApi.getBars)
    .mockResolvedValueOnce(dailyBarsFixture)
    .mockResolvedValueOnce(todayBarsFixture)
    .mockReturnValueOnce(new Promise((_, reject) => { rejectRefresh = reject; }));
  renderDetail();
  await act(async () => Promise.resolve());
  fireEvent.click(screen.getByRole("button", { name: "今日" }));
  await act(async () => Promise.resolve());
  expect(screen.getByTestId("kline-chart")).toHaveTextContent("1m:1");

  await act(async () => vi.advanceTimersByTime(60_000));
  expect(screen.getByTestId("kline-chart")).toHaveTextContent("1m:1");
  expect(screen.getByText("正在刷新 K 线…")).toBeInTheDocument();
  await act(async () => rejectRefresh?.(new Error("临时网络错误")));
  expect(screen.getByTestId("kline-chart")).toHaveTextContent("1m:1");
  expect(screen.getByRole("alert")).toHaveTextContent("刷新失败");
});

test("后端判定闭市或节假日时不重复请求今日 K 线", async () => {
  vi.useFakeTimers();
  vi.mocked(marketApi.getSummary).mockResolvedValue({ ...summaryFixture, market_status: "closed" });
  vi.mocked(marketApi.getBars)
    .mockResolvedValueOnce(dailyBarsFixture)
    .mockResolvedValueOnce(todayBarsFixture);
  renderDetail();
  await act(async () => Promise.resolve());
  fireEvent.click(screen.getByRole("button", { name: "今日" }));
  await act(async () => Promise.resolve());
  expect(marketApi.getBars).toHaveBeenCalledTimes(2);

  await act(async () => vi.advanceTimersByTime(60_000));
  await act(async () => vi.advanceTimersByTime(60_000));
  expect(marketApi.getSummary).toHaveBeenCalledTimes(2);
  expect(marketApi.getBars).toHaveBeenCalledTimes(2);
});

test("后端判定开市时每 60 秒刷新且不会请求重入", async () => {
  vi.useFakeTimers();
  let resolveRefresh: ((value: typeof todayBarsFixture) => void) | undefined;
  vi.mocked(marketApi.getSummary).mockResolvedValue({ ...summaryFixture, market_status: "open" });
  vi.mocked(marketApi.getBars)
    .mockResolvedValueOnce(dailyBarsFixture)
    .mockResolvedValueOnce(todayBarsFixture)
    .mockReturnValueOnce(new Promise((resolve) => { resolveRefresh = resolve; }))
    .mockResolvedValue(todayBarsFixture);
  renderDetail();
  await act(async () => Promise.resolve());
  fireEvent.click(screen.getByRole("button", { name: "今日" }));
  await act(async () => Promise.resolve());

  await act(async () => vi.advanceTimersByTime(60_000));
  expect(marketApi.getBars).toHaveBeenCalledTimes(3);
  await act(async () => vi.advanceTimersByTime(120_000));
  expect(marketApi.getBars).toHaveBeenCalledTimes(3);
  await act(async () => resolveRefresh?.(todayBarsFixture));
  await act(async () => vi.advanceTimersByTime(60_000));
  expect(marketApi.getBars).toHaveBeenCalledTimes(4);
});

test("显示上海时间、数据来源与质量状态，非法时间使用占位", async () => {
  vi.mocked(marketApi.getStock).mockResolvedValue({
    ...stockDetailFixture,
    quality_status: "stale",
    captured_at: "非法时间",
    latest_price: Number.NaN,
    change_amount: Number.POSITIVE_INFINITY,
    change_percent: null,
  });
  vi.mocked(marketApi.getBars).mockResolvedValue({
    ...dailyBarsFixture,
    source: "akshare",
    last_updated_at: "2026-08-04T07:00:00Z",
    items: [{ ...dailyBarsFixture.items[0], is_complete: false }],
  });
  renderDetail();

  expect(await screen.findByText("数据已过期")).toBeInTheDocument();
  expect(screen.getByText(/股票快照 时间未知/)).toBeInTheDocument();
  expect(screen.getByText(/K 线来源 akshare/)).toBeInTheDocument();
  expect(screen.getByText(/最后更新 2026-08-04 15:00:00/)).toBeInTheDocument();
  expect(screen.getByText("动态柱")).toBeInTheDocument();
  expect(screen.getByText("SH.600519 · 未知")).toBeInTheDocument();
  expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
});

test("涨跌额与涨跌幅分别按自身数值显示方向和正负号", async () => {
  vi.mocked(marketApi.getStock).mockResolvedValue({
    ...stockDetailFixture,
    change_amount: -0.18,
    change_percent: 1.25,
  });
  renderDetail();

  const amount = await screen.findByText("-0.18");
  const percent = screen.getByText("+1.25%");
  expect(amount).toHaveClass("down");
  expect(percent).toHaveClass("up");
  expect(amount.parentElement).toHaveTextContent("下跌");
  expect(percent.parentElement).toHaveTextContent("上涨");
});
