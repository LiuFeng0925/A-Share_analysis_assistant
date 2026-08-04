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
    "http://127.0.0.1:8000/api/market/summary",
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
