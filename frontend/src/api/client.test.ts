import { afterEach, expect, test, vi } from "vitest";
import { marketApi } from "./client";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("把外部取消信号传给 fetch，并保留 AbortError 语义", async () => {
  const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_, reject) => {
    init?.signal?.addEventListener("abort", () => {
      reject(new DOMException("请求已取消", "AbortError"));
    }, { once: true });
  }));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();

  const request = marketApi.getSummary({ signal: controller.signal });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/market/summary",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  controller.abort();

  await expect(request).rejects.toMatchObject({ name: "AbortError" });
});

test("超过指定时间会中止底层请求并给出中文超时错误", async () => {
  vi.useFakeTimers();
  let requestSignal: AbortSignal | null = null;
  const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_, reject) => {
    requestSignal = init?.signal ?? null;
    requestSignal?.addEventListener("abort", () => {
      reject(new DOMException("已中止", "AbortError"));
    }, { once: true });
  }));
  vi.stubGlobal("fetch", fetchMock);

  const request = marketApi.getSummary({ timeoutMs: 500 });
  const rejection = expect(request).rejects.toThrow("行情请求超时");
  expect(requestSignal).not.toBeNull();
  await vi.advanceTimersByTimeAsync(500);

  await rejection;
  expect(requestSignal).toHaveProperty("aborted", true);
});

test("收到响应头后解析响应体仍受同一个超时约束", async () => {
  vi.useFakeTimers();
  let requestSignal: AbortSignal | null = null;
  const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
    requestSignal = init?.signal ?? null;
    return Promise.resolve({
      ok: true,
      json: () => new Promise((_resolve, reject) => {
        requestSignal?.addEventListener("abort", () => {
          reject(new DOMException("已中止", "AbortError"));
        }, { once: true });
      }),
    } as Response);
  });
  vi.stubGlobal("fetch", fetchMock);

  const request = marketApi.getSummary({ timeoutMs: 500 });
  const rejection = expect(request).rejects.toThrow("行情请求超时");
  await vi.advanceTimersByTimeAsync(500);

  expect(requestSignal).toHaveProperty("aborted", true);
  await rejection;
});

test("HTTP 错误继续提供稳定的中文提示", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 503 })));

  await expect(marketApi.getSummary()).rejects.toThrow("行情接口失败：503");
});

test("股票列表请求会携带 MACD 与 KDJ 筛选参数", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ items: [], total: 0, page: 1, page_size: 50 })),
  );
  vi.stubGlobal("fetch", fetchMock);

  await marketApi.getStocks({
    query: "茅台",
    market: "SH",
    page: 1,
    pageSize: 50,
    sortBy: "code",
    sortOrder: "asc",
    macdSignal: "golden_cross",
    macdZeroAxis: "above",
    macdRecentWindow: "3d",
    kdjSignal: "golden_cross",
    kdjSignalZone: "low",
    kdjRecentWindow: "5d",
  });

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/market/stocks?query=%E8%8C%85%E5%8F%B0&market=SH&page=1&page_size=50&sort_by=code&sort_order=asc&macd_signal=golden_cross&macd_zero_axis=above&macd_recent_window=3d&kdj_signal=golden_cross&kdj_signal_zone=low&kdj_recent_window=5d",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

test("股票列表请求会重复携带 MACD 背离筛选参数", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ items: [], total: 0, page: 1, page_size: 50 })),
  );
  vi.stubGlobal("fetch", fetchMock);

  await marketApi.getStocks({
    page: 1,
    pageSize: 50,
    sortBy: "code",
    sortOrder: "asc",
    macdDivergences: ["bottom_forming", "top_confirmed"],
    macdDivergenceCross: "present",
    macdDivergenceRecentWindow: "20d",
  });

  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining(
      "macd_divergences=bottom_forming&macd_divergences=top_confirmed",
    ),
    expect.any(Object),
  );
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("macd_divergence_recent_window=20d"),
    expect.any(Object),
  );
});

test("MACD 指标接口携带指定 K 线周期", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ market: "SH", code: "600519", period: "1d", items: [] })),
  );
  vi.stubGlobal("fetch", fetchMock);

  await marketApi.getMacdIndicator("SH", "600519", "5m");

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/stocks/SH/600519/indicators/macd?period=5m",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

test("KDJ 指标接口携带指定 K 线周期和取消信号", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ market: "SH", code: "600519", period: "30m", items: [] })),
  );
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();

  await marketApi.getKdjIndicator("SH", "600519", "30m", { signal: controller.signal });

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/stocks/SH/600519/indicators/kdj?period=30m",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});
