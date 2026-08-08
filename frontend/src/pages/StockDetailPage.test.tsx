import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { marketApi } from "../api/client";
import {
  dailyBarsFixture,
  kdjIndicatorFixture,
  macdIndicatorFixture,
  stockDetailFixture,
  summaryFixture,
  todayBarsFixture,
} from "../test/fixtures";
import { StockDetailPage } from "./StockDetailPage";

vi.mock("../api/client", () => ({
  isAbortError: (error: unknown) => error instanceof DOMException && error.name === "AbortError",
  marketApi: {
    getStock: vi.fn(),
    getBars: vi.fn(),
    getSummary: vi.fn(),
    getStocks: vi.fn(),
    getMacdIndicator: vi.fn(),
    getKdjIndicator: vi.fn(),
  },
}));

vi.mock("../components/KlineChart", () => ({
  KlineChart: ({
    series,
    macd,
    kdj,
  }: {
    series: typeof dailyBarsFixture;
    macd?: typeof macdIndicatorFixture | null;
    kdj?: typeof kdjIndicatorFixture | null;
  }) => (
    <div data-testid="kline-chart">
      {series.period}:{series.items.length}:{macd?.summary.recent_signal_label ?? "无 MACD"}:{kdj?.summary.recent_signal_label ?? "无 KDJ"}
    </div>
  ),
}));

beforeEach(() => {
  vi.mocked(marketApi.getStock).mockReset().mockResolvedValue(stockDetailFixture);
  vi.mocked(marketApi.getBars).mockReset().mockResolvedValue(dailyBarsFixture);
  vi.mocked(marketApi.getSummary).mockReset().mockResolvedValue(summaryFixture);
  vi.mocked(marketApi.getStocks).mockReset().mockResolvedValue({
    total: 0,
    page: 1,
    page_size: 10,
    items: [],
  });
  vi.mocked(marketApi.getMacdIndicator).mockReset().mockResolvedValue(macdIndicatorFixture);
  vi.mocked(marketApi.getKdjIndicator).mockReset().mockResolvedValue(kdjIndicatorFixture);
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
  expect(marketApi.getStock).toHaveBeenCalledWith(
    "SH",
    "600519",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(marketApi.getBars).toHaveBeenCalledWith(
    {
      market: "SH",
      code: "600519",
      period: "1d",
      range: "60d",
      adjustment: "qfq",
    },
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(screen.getByRole("button", { name: "日K" })).toHaveAttribute("aria-pressed", "true");
});

test("详情页展示 MACD 结果并把日线指标传给 K 线副图", async () => {
  renderDetail();

  expect(await screen.findByText("MACD 指标 · 日K")).toBeInTheDocument();
  expect(screen.getByText("近 3 日金叉")).toBeInTheDocument();
  expect(screen.getByText("零轴线上")).toBeInTheDocument();
  expect(screen.getByText("金叉")).toBeInTheDocument();
  expect(screen.queryByText("DIFF 0.18")).not.toBeInTheDocument();
  expect(screen.queryByText("DEA 0.11")).not.toBeInTheDocument();
  expect(screen.queryByText("红绿柱")).not.toBeInTheDocument();
  expect(screen.queryByText(/信号日期/)).not.toBeInTheDocument();
  expect(screen.queryByText(/计算时间/)).not.toBeInTheDocument();
  expect(screen.getByTestId("kline-chart")).toHaveTextContent("近 3 日金叉");
  expect(marketApi.getMacdIndicator).toHaveBeenCalledWith(
    "SH",
    "600519",
    "1d",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

test("详情页展示 KDJ 数值、区域、信号时间并传给副图", async () => {
  renderDetail();

  expect(await screen.findByText("KDJ 指标 · 日K")).toBeInTheDocument();
  expect(screen.getByText("K 18.20")).toBeInTheDocument();
  expect(screen.getByText("D 17.10")).toBeInTheDocument();
  expect(screen.getByText("J 20.40")).toBeInTheDocument();
  expect(screen.getByText("超卖区")).toBeInTheDocument();
  expect(screen.getByText("低位")).toBeInTheDocument();
  expect(screen.getAllByText("盘中动态").length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText("2026-08-04 10:26:00").length).toBeGreaterThanOrEqual(1);
  expect(screen.getByTestId("kline-chart")).toHaveTextContent("盘中金叉");
  expect(marketApi.getKdjIndicator).toHaveBeenCalledWith(
    "SH",
    "600519",
    "1d",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

test("KDJ 信号状态跟随交叉柱，不被后续动态柱误标为盘中信号", async () => {
  vi.mocked(marketApi.getKdjIndicator).mockResolvedValue({
    ...kdjIndicatorFixture,
    summary: {
      ...kdjIndicatorFixture.summary,
      signal_time: "2026-08-01T15:00:00+08:00",
      recent_signal_label: "近 3 日金叉",
      is_intraday: true,
    },
    items: [
      {
        ...kdjIndicatorFixture.items[0],
        signal_type: "golden_cross",
        signal_zone: "low",
      },
      {
        ...kdjIndicatorFixture.items[1],
        signal_type: "none",
        signal_zone: "unknown",
      },
    ],
  });

  renderDetail();

  expect(await screen.findByText("KDJ 指标 · 日K")).toBeInTheDocument();
  expect(screen.getByText("已确认")).toBeInTheDocument();
});

test("切换到 30 分钟会同步请求 K 线、MACD 与 KDJ", async () => {
  vi.mocked(marketApi.getBars).mockResolvedValueOnce(dailyBarsFixture).mockResolvedValueOnce({
    ...dailyBarsFixture,
    period: "30m",
  });
  vi.mocked(marketApi.getMacdIndicator)
    .mockResolvedValueOnce(macdIndicatorFixture)
    .mockResolvedValueOnce({ ...macdIndicatorFixture, period: "30m" });
  vi.mocked(marketApi.getKdjIndicator)
    .mockResolvedValueOnce(kdjIndicatorFixture)
    .mockResolvedValueOnce({ ...kdjIndicatorFixture, period: "30m" });
  renderDetail();

  fireEvent.click(await screen.findByRole("button", { name: "30分" }));

  await waitFor(() => expect(marketApi.getKdjIndicator).toHaveBeenLastCalledWith(
    "SH",
    "600519",
    "30m",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  ));
  expect(await screen.findByText("KDJ 指标 · 30分")).toBeInTheDocument();
  expect(screen.getByTestId("kline-chart")).toHaveTextContent("30m");
});

test("30 分钟在开市时每 60 秒静默刷新 K 线和两个指标", async () => {
  vi.useFakeTimers();
  vi.mocked(marketApi.getSummary).mockResolvedValue({ ...summaryFixture, market_status: "open" });
  vi.mocked(marketApi.getBars).mockResolvedValue({ ...dailyBarsFixture, period: "30m" });
  vi.mocked(marketApi.getMacdIndicator).mockResolvedValue({
    ...macdIndicatorFixture,
    period: "30m",
  });
  vi.mocked(marketApi.getKdjIndicator).mockResolvedValue({
    ...kdjIndicatorFixture,
    period: "30m",
  });
  renderDetail();
  fireEvent.click(screen.getByRole("button", { name: "30分" }));
  await act(async () => Promise.resolve());

  const barsBefore = vi.mocked(marketApi.getBars).mock.calls.length;
  const macdBefore = vi.mocked(marketApi.getMacdIndicator).mock.calls.length;
  const kdjBefore = vi.mocked(marketApi.getKdjIndicator).mock.calls.length;
  await act(async () => vi.advanceTimersByTimeAsync(60_000));

  expect(marketApi.getBars).toHaveBeenCalledTimes(barsBefore + 1);
  expect(marketApi.getMacdIndicator).toHaveBeenCalledTimes(macdBefore + 1);
  expect(marketApi.getKdjIndicator).toHaveBeenCalledTimes(kdjBefore + 1);
  expect(screen.queryByText("正在计算 KDJ…")).not.toBeInTheDocument();
});

test("KDJ 静默刷新失败时保留上次成功结果", async () => {
  vi.useFakeTimers();
  vi.mocked(marketApi.getSummary).mockResolvedValue({ ...summaryFixture, market_status: "open" });
  vi.mocked(marketApi.getKdjIndicator)
    .mockResolvedValueOnce(kdjIndicatorFixture)
    .mockRejectedValueOnce(new Error("KDJ 临时失败"));
  renderDetail();
  await act(async () => Promise.resolve());
  expect(screen.getByText("K 18.20")).toBeInTheDocument();

  await act(async () => vi.advanceTimersByTimeAsync(60_000));

  expect(screen.getByText("K 18.20")).toBeInTheDocument();
  expect(screen.getByText(/KDJ 刷新失败/)).toBeInTheDocument();
});

test("切换 K 线周期后请求并展示同周期 MACD", async () => {
  vi.mocked(marketApi.getBars)
    .mockResolvedValueOnce(dailyBarsFixture)
    .mockResolvedValueOnce({ ...dailyBarsFixture, period: "5m", items: dailyBarsFixture.items });
  vi.mocked(marketApi.getMacdIndicator)
    .mockResolvedValueOnce(macdIndicatorFixture)
    .mockResolvedValueOnce({
      ...macdIndicatorFixture,
      period: "5m",
      summary: { ...macdIndicatorFixture.summary, recent_signal_label: "5分金叉" },
    });

  renderDetail();
  expect(await screen.findByTestId("kline-chart")).toHaveTextContent("1d:1:近 3 日金叉");

  fireEvent.click(screen.getByRole("button", { name: "5分" }));

  await waitFor(() => expect(marketApi.getMacdIndicator).toHaveBeenLastCalledWith(
    "SH",
    "600519",
    "5m",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  ));
  expect(await screen.findByTestId("kline-chart")).toHaveTextContent("5m:1:5分金叉");
  expect(screen.getByText("MACD 指标 · 5分")).toBeInTheDocument();
});

test("旧周期 MACD 不会传给新周期图表", async () => {
  let resolveFiveMinuteMacd: ((value: typeof macdIndicatorFixture) => void) | undefined;
  vi.mocked(marketApi.getBars)
    .mockResolvedValueOnce(dailyBarsFixture)
    .mockResolvedValueOnce({ ...dailyBarsFixture, period: "5m", items: dailyBarsFixture.items });
  vi.mocked(marketApi.getMacdIndicator)
    .mockResolvedValueOnce(macdIndicatorFixture)
    .mockReturnValueOnce(new Promise((resolve) => { resolveFiveMinuteMacd = resolve; }));

  renderDetail();
  expect(await screen.findByTestId("kline-chart")).toHaveTextContent("1d:1:近 3 日金叉");

  fireEvent.click(screen.getByRole("button", { name: "5分" }));
  await waitFor(() => expect(screen.getByTestId("kline-chart")).toHaveTextContent("5m:1:无 MACD"));
  expect(screen.getByText("正在计算 MACD…")).toBeInTheDocument();
  expect(screen.queryByText("近 3 日金叉")).not.toBeInTheDocument();

  await act(async () => resolveFiveMinuteMacd?.({
    ...macdIndicatorFixture,
    period: "5m",
    summary: { ...macdIndicatorFixture.summary, recent_signal_label: "5分金叉" },
  }));
  expect(screen.getByTestId("kline-chart")).toHaveTextContent("5m:1:5分金叉");
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
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
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
  expect(marketApi.getSummary).toHaveBeenCalledTimes(1);
  expect(marketApi.getBars).toHaveBeenCalledTimes(2);
});

test("市场从开市切换为闭市时完成最后一次刷新后停止轮询", async () => {
  vi.useFakeTimers();
  vi.mocked(marketApi.getSummary)
    .mockResolvedValueOnce(summaryFixture)
    .mockResolvedValue({ ...summaryFixture, market_status: "closed" });
  renderDetail();
  await act(async () => Promise.resolve());

  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  const barsAfterClose = vi.mocked(marketApi.getBars).mock.calls.length;
  const kdjAfterClose = vi.mocked(marketApi.getKdjIndicator).mock.calls.length;
  expect(barsAfterClose).toBe(2);
  expect(kdjAfterClose).toBe(2);

  await act(async () => vi.advanceTimersByTimeAsync(120_000));
  expect(marketApi.getBars).toHaveBeenCalledTimes(barsAfterClose);
  expect(marketApi.getKdjIndicator).toHaveBeenCalledTimes(kdjAfterClose);
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

test("市场状态恢复成功会清除旧刷新错误并在闭市后停止", async () => {
  vi.useFakeTimers();
  vi.mocked(marketApi.getSummary)
    .mockResolvedValueOnce(summaryFixture)
    .mockRejectedValueOnce(new Error("状态服务暂不可用"))
    .mockResolvedValue({ ...summaryFixture, market_status: "closed" });
  vi.mocked(marketApi.getBars)
    .mockResolvedValueOnce(dailyBarsFixture)
    .mockResolvedValueOnce(todayBarsFixture)
    .mockResolvedValue(dailyBarsFixture);
  renderDetail();
  await act(async () => Promise.resolve());
  fireEvent.click(screen.getByRole("button", { name: "今日" }));
  await act(async () => Promise.resolve());

  await act(async () => vi.advanceTimersByTime(60_000));
  expect(screen.getByRole("alert")).toHaveTextContent("自动刷新状态检查失败");
  await act(async () => vi.advanceTimersByTime(60_000));
  expect(screen.queryByText(/自动刷新状态检查失败/)).not.toBeInTheDocument();

  await act(async () => vi.advanceTimersByTime(120_000));
  expect(marketApi.getSummary).toHaveBeenCalledTimes(3);
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

test("详情展示完整快照字段和来自统一摘要的开闭市状态", async () => {
  renderDetail();

  expect(await screen.findByText("昨收")).toBeInTheDocument();
  expect(screen.getByText("1,552.32")).toBeInTheDocument();
  expect(screen.getByText("换手率")).toBeInTheDocument();
  expect(screen.getByText("0.30%")).toBeInTheDocument();
  expect(screen.getByText("总市值")).toBeInTheDocument();
  expect(screen.getByText("1.995 万亿")).toBeInTheDocument();
  expect(screen.getByText("开市（交易中）")).toBeInTheDocument();

  vi.mocked(marketApi.getSummary).mockResolvedValue({
    ...summaryFixture,
    market_status: "closed",
  });
  const { unmount } = renderDetail("/stocks/SZ/000001");
  expect(await screen.findByText("闭市（已收盘）")).toBeInTheDocument();
  unmount();
});

test("搜索其他股票支持键盘选择并切换详情", async () => {
  vi.mocked(marketApi.getStocks).mockResolvedValue({
    total: 1,
    page: 1,
    page_size: 10,
    items: [{ ...stockDetailFixture, market: "SZ", code: "000001", name: "平安银行" }],
  });
  renderDetail();
  const input = screen.getByRole("combobox", { name: "搜索其他股票" });

  fireEvent.change(input, { target: { value: "平安" } });
  const option = await screen.findByRole("option", { name: /平安银行 000001/ });
  expect(option).toBeInTheDocument();
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "Enter" });

  await waitFor(() => expect(marketApi.getStock).toHaveBeenLastCalledWith(
    "SZ",
    "000001",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  ));
});

test("关键词改变后立即隐藏旧候选且新响应前不能选择旧股票", async () => {
  vi.useFakeTimers();
  let resolveSecond: ((value: {
    total: number;
    page: number;
    page_size: number;
    items: typeof stockDetailFixture[];
  }) => void) | undefined;
  vi.mocked(marketApi.getStocks)
    .mockResolvedValueOnce({
      total: 1,
      page: 1,
      page_size: 10,
      items: [stockDetailFixture],
    })
    .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve; }));
  renderDetail();
  const input = screen.getByRole("combobox", { name: "搜索其他股票" });

  fireEvent.change(input, { target: { value: "茅台" } });
  await act(async () => vi.advanceTimersByTimeAsync(220));
  const staleOption = screen.getByRole("option", { name: /贵州茅台 600519/ });

  fireEvent.change(input, { target: { value: "平安" } });
  expect(screen.queryByRole("option", { name: /贵州茅台 600519/ })).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("正在搜索股票");
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.mouseDown(staleOption);
  expect(marketApi.getStock).toHaveBeenCalledTimes(1);

  await act(async () => vi.advanceTimersByTimeAsync(220));
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.mouseDown(staleOption);
  expect(marketApi.getStock).toHaveBeenCalledTimes(1);

  await act(async () => resolveSecond?.({
    total: 1,
    page: 1,
    page_size: 10,
    items: [{ ...stockDetailFixture, market: "SZ", code: "000001", name: "平安银行" }],
  }));
  expect(screen.getByRole("option", { name: /平安银行 000001/ })).toBeInTheDocument();
});

test("切股搜索会取消旧请求并忽略迟到结果，卸载时也清理在途请求", async () => {
  let resolveFirst: ((value: { total: number; page: number; page_size: number; items: typeof stockDetailFixture[] }) => void) | undefined;
  vi.mocked(marketApi.getStocks)
    .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }))
    .mockResolvedValueOnce({
      total: 1,
      page: 1,
      page_size: 10,
      items: [{ ...stockDetailFixture, market: "SZ", code: "000001", name: "平安银行" }],
    });
  const { unmount } = renderDetail();
  const input = screen.getByRole("combobox", { name: "搜索其他股票" });

  fireEvent.change(input, { target: { value: "茅" } });
  await waitFor(() => expect(marketApi.getStocks).toHaveBeenCalledTimes(1));
  const firstSignal = vi.mocked(marketApi.getStocks).mock.calls[0][1]?.signal;
  fireEvent.change(input, { target: { value: "平安" } });
  await waitFor(() => expect(marketApi.getStocks).toHaveBeenCalledTimes(2));
  expect(firstSignal?.aborted).toBe(true);
  resolveFirst?.({
    total: 1,
    page: 1,
    page_size: 10,
    items: [stockDetailFixture],
  });
  expect(await screen.findByRole("option", { name: /平安银行/ })).toBeInTheDocument();

  const secondSignal = vi.mocked(marketApi.getStocks).mock.calls[1][1]?.signal;
  unmount();
  expect(secondSignal?.aborted).toBe(true);
});
