import { act, renderHook } from "@testing-library/react";
import { usePolling } from "./usePolling";

afterEach(() => {
  vi.useRealTimers();
});

test("首次加载并仅在页面可见时按一分钟轮询", async () => {
  vi.useFakeTimers();
  const load = vi.fn();
  const visibilityState = vi.spyOn(document, "visibilityState", "get");
  visibilityState.mockReturnValue("visible");

  const { unmount } = renderHook(() => usePolling(load, 60_000));
  expect(load).toHaveBeenCalledTimes(1);

  await vi.advanceTimersByTimeAsync(60_000);
  expect(load).toHaveBeenCalledTimes(2);

  visibilityState.mockReturnValue("hidden");
  await vi.advanceTimersByTimeAsync(60_000);
  expect(load).toHaveBeenCalledTimes(2);

  unmount();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(load).toHaveBeenCalledTimes(2);
  visibilityState.mockRestore();
});

test("请求未完成时不会重入，完成后才接受下一次轮询", async () => {
  vi.useFakeTimers();
  let resolveFirst: (() => void) | undefined;
  const load = vi.fn().mockReturnValueOnce(new Promise<void>((resolve) => {
    resolveFirst = resolve;
  })).mockResolvedValue(undefined);

  renderHook(() => usePolling(load, 60_000));
  await vi.advanceTimersByTimeAsync(180_000);
  expect(load).toHaveBeenCalledTimes(1);

  await act(async () => resolveFirst?.());
  await vi.advanceTimersByTimeAsync(60_000);
  expect(load).toHaveBeenCalledTimes(2);
});

test("页面隐藏和卸载会取消在途请求，恢复可见后立即刷新", async () => {
  let visibility = "visible";
  const visibilityState = vi.spyOn(document, "visibilityState", "get")
    .mockImplementation(() => visibility as DocumentVisibilityState);
  let resolveFirst: (() => void) | undefined;
  const load = vi.fn()
    .mockImplementationOnce((_signal: AbortSignal) => new Promise<void>((resolve) => {
      resolveFirst = resolve;
    }))
    .mockReturnValueOnce(new Promise<void>(() => undefined));
  const { unmount } = renderHook(() => usePolling(load, 60_000));
  const firstSignal = load.mock.calls[0][0] as AbortSignal;

  visibility = "hidden";
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  expect(firstSignal.aborted).toBe(true);

  visibility = "visible";
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await act(async () => resolveFirst?.());
  await act(async () => Promise.resolve());
  expect(load).toHaveBeenCalledTimes(2);

  const secondSignal = load.mock.calls[1][0] as AbortSignal;
  unmount();
  expect(secondSignal.aborted).toBe(true);
  visibilityState.mockRestore();
});

test("依赖变化会取消旧请求，并可关闭首次立即加载", async () => {
  const firstLoad = vi.fn().mockReturnValue(new Promise<void>(() => undefined));
  const secondLoad = vi.fn().mockResolvedValue(undefined);
  const { rerender } = renderHook(
    ({ load, immediate }) => usePolling(load, 60_000, { immediate }),
    { initialProps: { load: firstLoad, immediate: true } },
  );
  const firstSignal = firstLoad.mock.calls[0][0] as AbortSignal;

  rerender({ load: secondLoad, immediate: false });

  expect(firstSignal.aborted).toBe(true);
  expect(secondLoad).not.toHaveBeenCalled();
});
