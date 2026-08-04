import { renderHook } from "@testing-library/react";
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
