import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { marketApi } from "../api/client";
import { summaryFixture, stockPageFixture } from "../test/fixtures";
import { StockListPage } from "./StockListPage";

vi.mock("../api/client", () => ({
  isAbortError: (error: unknown) => error instanceof DOMException && error.name === "AbortError",
  marketApi: { getSummary: vi.fn(), getStocks: vi.fn() },
}));

beforeEach(() => {
  vi.mocked(marketApi.getSummary).mockReset().mockResolvedValue(summaryFixture);
  vi.mocked(marketApi.getStocks).mockReset().mockResolvedValue(stockPageFixture);
  window.sessionStorage.clear();
});

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前列表地址">{`${location.pathname}${location.search}`}</output>;
}

test("一只股票只占一行并在防抖后按名称搜索", async () => {
  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  expect(await screen.findByRole("row", { name: /贵州茅台/ })).toBeInTheDocument();
  expect(screen.getAllByRole("row", { name: /贵州茅台/ })).toHaveLength(1);

  fireEvent.change(screen.getByPlaceholderText("搜索股票代码或名称"), {
    target: { value: "茅台" },
  });

  expect(marketApi.getStocks).toHaveBeenCalledTimes(1);
  await waitFor(() =>
    expect(marketApi.getStocks).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: "茅台", page: 1 }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ),
  );
});

test("显示真实概览、数据过期提示与最后更新时间", async () => {
  vi.mocked(marketApi.getSummary).mockResolvedValue({ ...summaryFixture, stale: true });

  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  expect(await screen.findByText("5,314")).toBeInTheDocument();
  expect(screen.getByText("数据可能已过期")).toBeInTheDocument();
  expect(screen.getByText(/最后更新/)).toHaveTextContent("10:26:00");
});

test("概览未知时显示正在确认，非法时间显示占位符", async () => {
  let resolveSummary: ((value: typeof summaryFixture) => void) | undefined;
  vi.mocked(marketApi.getSummary).mockReturnValueOnce(
    new Promise((resolve) => {
      resolveSummary = resolve;
    }),
  );
  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  expect(screen.getByText("正在确认")).toBeInTheDocument();
  resolveSummary?.({ ...summaryFixture, last_updated_at: "不是合法时间" });
  expect(await screen.findByText(/最后更新/)).toHaveTextContent("时间未知");
});

test("支持市场筛选、排序和分页", async () => {
  vi.mocked(marketApi.getStocks).mockResolvedValue({
    ...stockPageFixture,
    total: 83,
  });
  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  await screen.findByRole("row", { name: /贵州茅台/ });
  fireEvent.change(screen.getByLabelText("市场筛选"), { target: { value: "SH" } });
  await waitFor(() =>
    expect(marketApi.getStocks).toHaveBeenLastCalledWith(
      expect.objectContaining({ market: "SH", page: 1 }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ),
  );

  fireEvent.click(screen.getByRole("button", { name: "按涨跌幅排序" }));
  await waitFor(() =>
    expect(marketApi.getStocks).toHaveBeenLastCalledWith(
      expect.objectContaining({ sortBy: "change_percent", sortOrder: "desc" }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ),
  );

  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() =>
    expect(marketApi.getStocks).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2 }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ),
  );
});

test("MACD 筛选收敛为单行控件并移除说明文案", async () => {
  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  await screen.findByRole("row", { name: /贵州茅台/ });
  expect(screen.getByText("日 K MACD 雷达")).toBeInTheDocument();
  expect(screen.queryByText("近 5 个交易日内，按日 K 的最后一次 MACD 交叉信号筛选。")).not.toBeInTheDocument();
  expect(screen.getByRole("option", { name: "近 5 日金叉" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "近 5 日死叉" })).toBeInTheDocument();
  expect(screen.queryByLabelText("出现时间")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("背离对应交叉")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("背离出现时间")).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("日 K MACD 信号"), {
    target: { value: "golden_cross:3d" },
  });
  fireEvent.change(screen.getByLabelText("零轴位置"), {
    target: { value: "above" },
  });

  await waitFor(() =>
    expect(marketApi.getStocks).toHaveBeenLastCalledWith(
      expect.objectContaining({
        macdSignal: "golden_cross",
        macdZeroAxis: "above",
        macdRecentWindow: "3d",
        page: 1,
      }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ),
  );
});

test("支持组合 MACD 背离筛选", async () => {
  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  await screen.findByRole("row", { name: /贵州茅台/ });
  fireEvent.click(screen.getByRole("button", { name: "MACD 背离：不限" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "底背离形成中" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "顶背离已确认" }));

  await waitFor(() => expect(marketApi.getStocks).toHaveBeenLastCalledWith(
    expect.objectContaining({
      macdDivergences: ["bottom_forming", "top_confirmed"],
    }),
    expect.any(Object),
  ));
  expect(screen.getByRole("button", { name: "MACD 背离：已选 2 项" })).toBeInTheDocument();
});

test("MACD 背离支持最近出现时间窗口筛选", async () => {
  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  await screen.findByRole("row", { name: /贵州茅台/ });
  expect(screen.getByLabelText("背离时间")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "近 20 日" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "MACD 背离：不限" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "底背离已确认" }));
  fireEvent.change(screen.getByLabelText("背离时间"), { target: { value: "20d" } });

  await waitFor(() => expect(marketApi.getStocks).toHaveBeenLastCalledWith(
    expect.objectContaining({
      macdDivergences: ["bottom_confirmed"],
      macdDivergenceRecentWindow: "20d",
    }),
    expect.any(Object),
  ));
});

test("清空指标会清空 MACD 背离筛选", async () => {
  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  await screen.findByRole("row", { name: /贵州茅台/ });
  fireEvent.click(screen.getByRole("button", { name: "MACD 背离：不限" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "底背离已确认" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "清空指标" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "清空指标" }));

  await waitFor(() => expect(marketApi.getStocks).toHaveBeenLastCalledWith(
    expect.objectContaining({
      macdDivergences: undefined,
      macdDivergenceRecentWindow: undefined,
    }),
    expect.any(Object),
  ));
});

test("从地址参数恢复筛选状态并请求对应列表", async () => {
  render(
    <MemoryRouter initialEntries={[
      "/?query=%E8%8C%85%E5%8F%B0&market=SH&page=2&sort_by=amount&sort_order=desc&macd_signal=golden_cross&macd_recent_window=3d&macd_zero_axis=above&macd_divergences=bottom_forming&macd_divergences=top_confirmed&macd_divergence_recent_window=10d",
    ]}>
      <Routes>
        <Route path="/" element={<><LocationProbe /><StockListPage /></>} />
      </Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByRole("row", { name: /贵州茅台/ })).toBeInTheDocument();
  expect(screen.getByPlaceholderText("搜索股票代码或名称")).toHaveValue("茅台");
  expect(screen.getByLabelText("市场筛选")).toHaveValue("SH");
  expect(screen.getByLabelText("日 K MACD 信号")).toHaveValue("golden_cross:3d");
  expect(screen.getByLabelText("零轴位置")).toHaveValue("above");
  expect(screen.getByLabelText("背离时间")).toHaveValue("10d");
  expect(screen.getByRole("button", { name: "MACD 背离：已选 2 项" })).toBeInTheDocument();
  expect(screen.getByLabelText("当前列表地址")).toHaveTextContent("query=%E8%8C%85%E5%8F%B0");
  expect(marketApi.getStocks).toHaveBeenLastCalledWith(
    expect.objectContaining({
      query: "茅台",
      market: "SH",
      page: 2,
      sortBy: "amount",
      sortOrder: "desc",
      macdSignal: "golden_cross",
      macdRecentWindow: "3d",
      macdZeroAxis: "above",
      macdDivergences: ["bottom_forming", "top_confirmed"],
      macdDivergenceRecentWindow: "10d",
    }),
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

test("返回列表页时恢复对应筛选地址的浏览位置", async () => {
  const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  window.sessionStorage.setItem(
    "stock-list-scroll:/?market=SZ&macd_signal=death_cross&macd_recent_window=5d",
    "432",
  );

  render(
    <MemoryRouter initialEntries={["/?market=SZ&macd_signal=death_cross&macd_recent_window=5d"]}>
      <StockListPage />
    </MemoryRouter>,
  );

  expect(await screen.findByRole("row", { name: /贵州茅台/ })).toBeInTheDocument();
  expect(screen.getByLabelText("市场筛选")).toHaveValue("SZ");
  expect(screen.getByLabelText("日 K MACD 信号")).toHaveValue("death_cross:5d");
  expect(scrollTo).toHaveBeenCalledWith({ top: 432, left: 0, behavior: "auto" });

  scrollTo.mockRestore();
});

test("筛选操作会同步到列表地址，供详情返回时复用", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<><LocationProbe /><StockListPage /></>} />
      </Routes>
    </MemoryRouter>,
  );

  await screen.findByRole("row", { name: /贵州茅台/ });
  fireEvent.change(screen.getByLabelText("市场筛选"), { target: { value: "SH" } });
  fireEvent.change(screen.getByLabelText("日 K MACD 信号"), { target: { value: "golden_cross:5d" } });
  fireEvent.click(screen.getByRole("button", { name: "MACD 背离：不限" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "底背离已确认" }));
  fireEvent.change(screen.getByLabelText("背离时间"), { target: { value: "5d" } });

  await waitFor(() => expect(screen.getByLabelText("当前列表地址")).toHaveTextContent("market=SH"));
  expect(screen.getByLabelText("当前列表地址")).toHaveTextContent("macd_signal=golden_cross");
  expect(screen.getByLabelText("当前列表地址")).toHaveTextContent("macd_recent_window=5d");
  expect(screen.getByLabelText("当前列表地址")).toHaveTextContent("macd_divergences=bottom_confirmed");
  expect(screen.getByLabelText("当前列表地址")).toHaveTextContent("macd_divergence_recent_window=5d");
});

test("股票行可使用 Enter 键进入详情", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<StockListPage />} />
        <Route path="/stocks/:market/:code" element={<p>已进入贵州茅台详情</p>} />
      </Routes>
    </MemoryRouter>,
  );

  const row = await screen.findByRole("row", { name: /贵州茅台/ });
  fireEvent.keyDown(row, { key: "Enter" });
  expect(await screen.findByText("已进入贵州茅台详情")).toBeInTheDocument();
});

test("接口失败显示中文错误且可重新加载", async () => {
  vi.mocked(marketApi.getStocks)
    .mockRejectedValueOnce(new Error("网络错误"))
    .mockResolvedValueOnce(stockPageFixture);

  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent("行情加载失败");
  fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
  expect(await screen.findByRole("row", { name: /贵州茅台/ })).toBeInTheDocument();
});

test("刷新当前页时保留已有股票", async () => {
  let resolveRefresh: ((value: typeof stockPageFixture) => void) | undefined;
  const refreshResponse = new Promise<typeof stockPageFixture>((resolve) => {
    resolveRefresh = resolve;
  });
  vi.mocked(marketApi.getStocks)
    .mockResolvedValueOnce(stockPageFixture)
    .mockReturnValueOnce(refreshResponse);

  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  expect(await screen.findByRole("row", { name: /贵州茅台/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "按最新价排序" }));
  expect(await screen.findByRole("status", { name: "" })).toHaveTextContent("正在刷新");
  expect(screen.getByRole("row", { name: /贵州茅台/ })).toBeInTheDocument();
  resolveRefresh?.(stockPageFixture);
});

test("较早的异步响应不会覆盖较新的查询结果", async () => {
  let resolveFirst: ((value: typeof stockPageFixture) => void) | undefined;
  const firstResponse = new Promise<typeof stockPageFixture>((resolve) => {
    resolveFirst = resolve;
  });
  const newerPage = {
    ...stockPageFixture,
    items: [{ ...stockPageFixture.items[0], name: "更新后的贵州茅台" }],
  };
  vi.mocked(marketApi.getStocks)
    .mockReturnValueOnce(firstResponse)
    .mockResolvedValueOnce(newerPage);

  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByPlaceholderText("搜索股票代码或名称"), {
    target: { value: "600519" },
  });

  expect(await screen.findByText("更新后的贵州茅台")).toBeInTheDocument();
  resolveFirst?.(stockPageFixture);
  await Promise.resolve();
  expect(screen.getByText("更新后的贵州茅台")).toBeInTheDocument();
  expect(screen.queryByText(/^贵州茅台$/)).not.toBeInTheDocument();
});

test("查询条件变化会取消上一批列表与摘要请求", async () => {
  let resolveFirst: ((value: typeof stockPageFixture) => void) | undefined;
  vi.mocked(marketApi.getStocks)
    .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }))
    .mockResolvedValue(stockPageFixture);
  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );
  const firstOptions = vi.mocked(marketApi.getStocks).mock.calls[0][1] as { signal: AbortSignal };

  fireEvent.change(screen.getByPlaceholderText("搜索股票代码或名称"), {
    target: { value: "600519" },
  });

  await waitFor(() => expect(marketApi.getStocks).toHaveBeenCalledTimes(2));
  expect(firstOptions.signal.aborted).toBe(true);
  resolveFirst?.(stockPageFixture);
});

test("取消请求不会显示为行情错误", async () => {
  vi.mocked(marketApi.getStocks).mockRejectedValueOnce(
    new DOMException("请求已取消", "AbortError"),
  );
  render(
    <MemoryRouter>
      <StockListPage />
    </MemoryRouter>,
  );

  await waitFor(() => expect(marketApi.getStocks).toHaveBeenCalled());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
