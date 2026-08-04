import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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
});

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
